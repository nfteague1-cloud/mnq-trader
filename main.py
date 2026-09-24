import datetime
import json
import os
import requests
from fastapi import FastAPI, Request
from google import genai

app = FastAPI()

# Credentials Configuration (Loaded securely via Render Environment Variables)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TRADERSPOST_WEBHOOK_URL = os.environ.get("TRADERSPOST_WEBHOOK_URL")

# Initialize the GenAI Client (automatically picks up GEMINI_API_KEY if not explicitly passed)
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else genai.Client()


@app.post("/webhook")
async def handle_tradingview_alert(request: Request):
  data = await request.json()

  # 1. Prime Execution Window Filter (06:30 AM - 08:30 AM PDT)
  now_pdt = datetime.datetime.now(
      datetime.timezone(datetime.timedelta(hours=-7))
  )
  start_time = now_pdt.replace(hour=6, minute=30, second=0, microsecond=0)
  end_time = now_pdt.replace(hour=8, minute=30, second=0, microsecond=0)

  if not (start_time <= now_pdt <= end_time):
    return {
        "status": "Outside Prime Execution Window (06:30-08:30 PDT). Holding"
                  " flat."
    }

  # 2. Autonomous Prompting with Multi-Indicator Context
  prompt = f"""
    You are an autonomous quantitative futures trading model evaluating 5-minute Micro E-mini Nasdaq (MNQ) candle metrics.
    
    Incoming Candle Metrics:
    - Time: {data.get('time')}
    - Price Action (OHLC): Open: {data.get('open')}, High: {data.get('high')}, Low: {data.get('low')}, Close: {data.get('close')}
    - Volume: {data.get('volume')} (20 SMA Volume: {data.get('vol_sma')})
    - Trend & Momentum: 9 EMA: {data.get('ema_9')} | VWAP: {data.get('vwap')} | 14 RSI: {data.get('rsi_14')}
    - Directional Strength: ADX: {data.get('adx')} | +DI: {data.get('plus_di')} | -DI: {data.get('minus_di')}
    - Context: 14-day ADR: {data.get('adr')} | PM High: {data.get('pm_high')} | PM Low: {data.get('pm_low')}

    Analysis Rules (RTH Morning Window 06:30 - 08:30 AM PDT):
    1. Trend Strength Check: If ADX < 20, market is choppy. Do NOT initiate trend breakout trades unless volume exceeds 1.5x 20 SMA Volume.
    2. Directional Alignment: 
       - Long setups require: Close > VWAP, Close > 9 EMA, RSI > 55, +DI > -DI, and ADX > 20.
       - Short setups require: Close < VWAP, Close < 9 EMA, RSI < 45, -DI > +DI, and ADX > 20.
    3. Volume Confirmation: Ensure current Volume > Volume SMA on breakout candles beyond Premarket High/Low.
    4. Trap Avoidance: If price sweeps PM High ({data.get('pm_high')}) or PM Low ({data.get('pm_low')}) on low volume and ADX < 20, identify as a liquidity trap and stay 'flat'.

    Respond ONLY in raw valid JSON (no markdown block formatting):
    {{
        "signal": "buy" | "sell" | "flat",
        "action": "buy" | "sell" | "exit",
        "ticker": "MNQ",
        "quantity": 1,
        "rationale": "Brief sentence incorporating ADX, DMI, Volume, and PM levels"
    }}
    """

  # 3. Generate AI Analysis using the new Google GenAI SDK
  response = client.models.generate_content(
      model="gemini-1.5-pro",
      contents=prompt,
  )

  clean_json = response.text.replace("```json", "").replace("```", "").strip()
  decision = json.loads(clean_json)

  # 4. Order Routing to TradersPost
  if decision.get("signal") in ["buy", "sell"]:
    tp_payload = {
        "ticker": decision.get("ticker", "MNQ"),
        "action": decision.get("action"),
        "quantity": decision.get("quantity", 1),
        "sentiment": (
            "bullish" if decision.get("action") == "buy" else "bearish"
        ),
    }
    headers = {"Content-Type": "application/json"}
    r = requests.post(TRADERSPOST_WEBHOOK_URL, json=tp_payload, headers=headers)
    return {
        "status": "Order Transmitted",
        "traderspost_code": r.status_code,
        "ai_rationale": decision.get("rationale"),
    }

  return {
      "status": "Flat - No Trade Executed",
      "ai_rationale": decision.get("rationale"),
  }
