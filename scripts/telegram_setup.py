"""Connect the bot to your Telegram: checks the token, finds your chat id, sends a test message.

Before running: put your token in .env, open your bot in Telegram and press Start
(or send it any message).

Usage:  .venv\\Scripts\\python scripts\\telegram_setup.py
"""
import sys

from forexbot.telegram import Telegram, TelegramError, set_env_value

sys.stdout.reconfigure(encoding="utf-8")


def main():
    try:
        tg = Telegram()
        bot = tg.me()
        print(f"Token OK: connected to @{bot['username']} ({bot['first_name']})")

        chats = {}
        for u in tg.updates():
            msg = u.get("message") or u.get("my_chat_member") or {}
            chat = msg.get("chat") or {}
            if chat.get("type") == "private":
                chats[chat["id"]] = chat.get("first_name", "") + " " + chat.get("last_name", "")
        if not chats:
            print(f"\nNo messages found. In Telegram open @{bot['username']}, press START "
                  f"(or send 'hi'), then run this again.")
            return 1
        if len(chats) > 1:
            print("\nMore than one person has messaged this bot:")
            for cid, name in chats.items():
                print(f"  {cid}  {name.strip()}")
            print("Put YOUR id in TELEGRAM_CHAT_ID in .env manually.")
            return 1

        chat_id, name = next(iter(chats.items()))
        set_env_value("TELEGRAM_CHAT_ID", str(chat_id))
        print(f"Found your chat ({name.strip()}), saved to .env")
        tg.chat_id = str(chat_id)
        tg.send("✅ <b>Forex Bot connected.</b>\n\nThis is where your trade signals, "
                "risk alerts and close warnings will arrive.\n\n<i>Signals are experimental until a "
                "strategy passes the full backtest. Demo account only.</i>")
        print("Test message sent. Check your phone.")
        return 0
    except TelegramError as e:
        print(f"Problem: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
