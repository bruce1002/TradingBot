# TradingBot

## Run with Docker Compose

1. Copy env file:
   - `cp app/tv-binance-bot/.env.example app/tv-binance-bot/.env`
2. Edit `.env` with your keys.
3. Start:
   - `docker compose up -d --build`
4. Test:
   - `curl http://127.0.0.1:8000/dashboard`
