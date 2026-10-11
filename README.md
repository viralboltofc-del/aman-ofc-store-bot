AMAN OFC STORE — Railway Ready Build

This is the latest fixed bot build, prepared for Railway.

Includes the latest fixes:
- Editable Confirm Your Order description with placeholders
- Product name placeholder support
- Product Back button returns to that product's price list
- Editable Order/QR Cancelled Description
- Editable Order/QR Expired Description
- Add Balance confirmation description with {amount}
- UPI payment/coupon buttons without emojis as requested
- Existing shop, payment, reseller/API, wallet, admin, custom emoji/formatting and other features preserved

RAILWAY SETUP
1. Upload this project/ZIP to GitHub or deploy the folder to Railway.
2. In Railway Variables, add:
   BOT_TOKEN = your working BotFather token
3. Deploy as a Worker service.
4. Start command is already configured as:
   python aman_ofc_store_bot.py

IMPORTANT
- Do NOT commit bot_token.txt or put the bot token in source code.
- Railway Variables should contain BOT_TOKEN.
- If using SQLite and you need user balances/orders/data to survive redeploys, attach a persistent Railway Volume and keep the SQLite database on that persistent path. Do not run another copy of the same bot token elsewhere at the same time, or Telegram polling will conflict.
