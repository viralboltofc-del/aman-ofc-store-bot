"""
AMAN OFC STORE — Telegram Bot (chat-buttons version, no website/Mini App)
Everything happens directly inside the Telegram chat using inline buttons —
Shop, Wallet, Lucky Wheel, Referral, Orders, Profile, Support — exactly like
the reference video, no external site/webapp needed.

Fill in the CONFIG section below before running/uploading.
"""

import time
import random
import string
import logging
import sqlite3
import asyncio
import re
import threading
import shutil
from io import BytesIO
from urllib.parse import quote, unquote

import qrcode
import requests
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, MessageEntity, BotCommand, BotCommandScopeChat,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, TypeHandler, ContextTypes, filters,
)
from telegram.error import Conflict, BadRequest, NetworkError, TimedOut
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# CONFIG — fill these in before running
# ============================================================
# Bot token: put your WORKING token in bot_token.txt (same folder) or set BOT_TOKEN environment variable.
# The previous embedded token was rejected by Telegram, so it is intentionally not hard-coded here.
import os
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_token.txt")

def load_bot_token():
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token and os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            token = f.read().strip()
    if not token:
        raise RuntimeError("Bot token missing. Put the working BotFather token in bot_token.txt")
    return token

BOT_TOKEN = load_bot_token()
ADMIN_IDS = [6513926862]                   # your Telegram numeric user ID
STORE_NAME = "AMAN OFC STORE"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Railway: set DB_PATH=/data/store.db and attach a Persistent Volume at /data.
# Local/default: keep using store.db beside this bot file.
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "store.db"))
BOT_USERNAME = "@AMANOFCSTORE_BOT"       # fallback; Admin Panel saved username is used everywhere

def get_bot_username():
    """Return the admin-saved bot username, falling back to the configured default."""
    saved = str(get_setting("bot_username", "") or "").strip().lstrip("@")
    return "@" + saved if saved else BOT_USERNAME

# Unique private users who have interacted with this bot since the current
# bot process started. This is intentionally unlimited and resets only when
# the bot process is restarted.
SESSION_USERS = set()

# Internal ops groups — the bot must be added as a member/admin of each group.
# Use the /groupid command inside each group (as admin) to get its numeric ID.
NEW_USER_LOG_GROUP_ID = 0      # "New user details by aman"
KEY_LOG_GROUP_ID = 0           # "All key details by aman"
REFERRAL_LOG_GROUP_ID = 0      # "Reffer user details by aman"
REQUIRE_PHONE_VERIFICATION = True   # default — overridden by the Admin Panel toggle once the bot has run once
# ============================================================


def is_verification_required():
    """Whether new users must share their phone number before using the bot —
    toggleable live via Admin Panel → 📱 Verify Required: ON/OFF. Falls back to
    the REQUIRE_PHONE_VERIFICATION constant above until an admin changes it."""
    return get_setting("require_phone_verification", "1" if REQUIRE_PHONE_VERIFICATION else "0") == "1"

DAILY_WHEEL_PRIZES = [0, 3, 0, 2, 0, 5, 0, 0]  # ₹ amounts for the lucky wheel


# ============================================================
# BUTTON COLOR SYSTEM (Bot API 9.4, Feb 2026 — button `style` field)
# Only 3 real colors exist on Telegram: primary (blue), success (green),
# danger (red). "default" means no style at all (Telegram's normal grey).
# Every colorable button in this bot is listed in BUTTON_REGISTRY below.
# Admin changes are stored in the button_styles table and applied live via
# apply_button_colors()/IKM() — every keyboard in the bot goes through it.
# ============================================================

BUTTON_COLOR_CHOICES = [
    ("default", "⚪ Default", None),
    ("primary", "🔵 Blue", "primary"),
    ("success", "🟢 Green", "success"),
    ("danger", "🔴 Red", "danger"),
]

# (key, label shown in admin panel, group, original/default style)
BUTTON_REGISTRY = [
    ("menu:shop", "🛒 Shop Now", "Main Menu", "success"),
    ("menu:orders", "📝 My Orders", "Main Menu", None),
    ("menu:profile", "👤 Profile", "Main Menu", None),
    ("menu:balance", "💰 Add Balance", "Main Menu", "success"),
    ("menu:premium", "👑 Reseller", "Main Menu", "primary"),
    ("menu:wheel", "🎡 Lucky Wheel", "Main Menu", None),
    ("menu:updates", "📣 Updates", "Main Menu", None),
    ("menu:howto", "📺 How to Use", "Main Menu", None),
    ("menu:support", "💬 Support", "Main Menu", None),
    ("menu:share", "🎉 Share Bot", "Main Menu", None),
    ("menu:referral", "🎁 Refer & Earn", "Main Menu", "success"),
    ("menu:verify", "📱 Verify Account", "Main Menu", "primary"),
    ("menu:profilekeys", "👑 My Profile + Key History", "Main Menu", "success"),
    ("profile:addfund", "💰 Add Fund", "Profile", "success"),
    ("mykeys:purchased", "🔑 Purchased Keys", "My Keys", "success"),
    ("mykeys:notpurchased", "🛍️ Not Purchased", "My Keys", "primary"),
    ("mykeys:back", "⬅ Back", "My Keys", None),
    ("order:joinchannel", "UPDATE CHANNEL", "Order / Key", "primary"),
    ("maintenance:checkupdates", "CHECK UPDATES", "Product Maintenance", "primary"),
    ("howto:setup", "HOW TO USE SETUP", "How To / Support", "primary"),
    ("support:contactadmin", "CONTACT ADMIN", "How To / Support", "success"),
    ("admin:main", "⚙️ Admin Panel", "Main Menu", "primary"),
    ("admin:addproduct", "➕ Add Product", "Admin Panel", None),
    ("admin:addplan", "📅 Add Plan / Duration", "Admin Panel", None),
    ("admin:removeproduct", "🗑️ Remove Product", "Admin Panel", None),
    ("admin:editprice", "✏️ Edit Price", "Admin Panel", None),
    ("admin:editdesc", "📝 Edit Description", "Admin Panel", None),
    ("admin:setdelivery", "🔌 Set Delivery Mode", "Admin Panel", None),
    ("admin:manageproducts", "📦 Products", "Admin Panel", None),
    ("admin:setcategoryicon", "🗂️ Set Category Icon", "Admin Panel", None),
    ("admin:setapiduration", "⏱️ Set Plan API Duration", "Admin Panel", None),
    ("admin:setresellerprice", "👑 Fix Price for Resellers", "Admin Panel", None),
    ("admin:setpid", "🔢 Set Product PID", "Admin Panel", None),
    ("admin:seticon", "🎨 Set Product Icon", "Admin Panel", None),
    ("admin:setphoto", "🖼️ Set Product Photo", "Admin Panel", None),
    ("admin:managebuttons", "🧩 Manage Buttons", "Admin Panel", None),
    ("admin:hidebuttons", "👁️ Show/Hide Buttons", "Admin Panel", None),
    ("admin:addcustombtn", "➕ Add Custom Button", "Admin Panel", None),
    ("admin:removecustombtn", "🗑️ Remove Custom Button", "Admin Panel", None),
    ("admin:setresellerkey", "🔑 Change Reseller API Key", "Admin Panel", None),
    ("admin:setresellermasterkey", "🔐 Change Reseller Master Key", "Admin Panel", None),
    ("admin:setzapupikey", "💳 Change ZapUPI Key", "Admin Panel", None),
    ("admin:toggleverify", "📱 Verify Required: ON/OFF", "Admin Panel", None),
    ("admin:setfamkey", "💳 Change FAM API Key", "Admin Panel", None),
    ("admin:pendingorders", "📦 Pending Orders", "Admin Panel", None),
    ("admin:broadcast", "📢 Broadcast Message", "Admin Panel", None),
    ("admin:smsguide", "📩 SMS Auto-Verify Setup Guide", "Admin Panel", None),
    ("admin:setchannel", "📢 Change Update Channel", "Admin Panel", None),
    ("admin:setupi", "💳 Change UPI ID", "Admin Panel", None),
    ("admin:sethowto", "📺 Change How To Text", "Admin Panel", None),
    ("admin:sethowtovideo", "🎬 Set How To Video Link", "Admin Panel", None),
    ("admin:setbotusername", "🤖 Set Bot Username", "Admin Panel", None),
    ("admin:setadmincontact", "📞 Set Contact Admin Username", "Admin Panel", None),
    ("admin:setpremium", "👑 Premium Settings", "Admin Panel", None),
    ("admin:addadmin", "👑 Add Admin", "Admin Panel", None),
    ("admin:removeadmin", "🗑️ Remove Admin", "Admin Panel", None),
    ("admin:addreseller", "👑 Add Reseller (User)", "Admin Panel", None),
    ("admin:removereseller", "🚫 Remove Reseller (User)", "Admin Panel", None),
    ("admin:addbalanceuser", "💰 Add Balance User", "Admin Panel", None),
    ("admin:listadmins", "📋 List Admins", "Admin Panel", None),
    ("admin:coupons", "🎟️ Coupon Codes", "Admin Panel", None),
    ("admin:createcoupon", "➕ Create Coupon", "Admin Panel", None),
    ("admin:listcoupons", "📋 List Coupons", "Admin Panel", None),
    ("delcoupon", "❌ Delete Coupon", "Admin Panel", "danger"),
    ("deliver", "✅ Confirm & Deliver", "Orders", "success"),
    ("cancelorder", "❌ Cancel Order", "Orders", "danger"),
    ("delivermanual", "✏️ Manual Deliver Karein", "Orders", None),
    ("payplan:wallet", "Pay with Wallet", "Payment", "success"),
    ("payplan:upi", "Pay via UPI", "Payment", "primary"),
    ("applycoupon", "Apply Coupon Code", "Payment", None),
    ("removecoupon", "Remove Coupon", "Payment", "danger"),
    ("topup:100", "₹100", "Add Balance", None),
    ("topup:500", "₹500", "Add Balance", None),
    ("topup:1000", "₹1000", "Add Balance", None),
    ("topup:2000", "₹2000", "Add Balance", None),
    ("topup:custom", "✏️ Custom Amount", "Add Balance", "success"),
    ("kpadnum", "🔢 Add Balance → Number pad digits (0-9)", "Item Lists", None),
    ("kpadclear", "🔴 Add Balance → Clear (C)", "Payment", "danger"),
    ("kpadbksp", "⌫ Add Balance → Backspace", "Payment", "danger"),
    ("kpadconfirm", "✅ Add Balance → Confirm (use {amount})", "Payment", "success"),
    ("kpadback", "✋ Add Balance → Back", "Payment", "danger"),
    ("purchaseproof:openbot", "🛍️ Open Bot / Buy Product ↗️", "Purchase Proof", "primary"),
    ("sellingproofs:mainchannel", "Main Channel", "Selling Proofs", "primary"),
    ("sellingproofs:channel", "Selling Proofs", "Selling Proofs", "success"),
    ("topupdone", "✅ Maine Payment Kar Diya", "Payment", None),
    ("verifypay", "✅ Verify Payment", "Payment", "success"),
    ("topupapprove", "✅ Approve (topup)", "Payment", "success"),
    ("topupreject", "❌ Reject (topup)", "Payment", "danger"),
    ("qrcancel_order", "❌ Cancel (Product QR)", "Payment", "danger"),
    ("qrcancel_topup", "❌ Cancel (Add Balance QR)", "Payment", "danger"),
    ("buy", "💳 Buy Premium", "Payment", "success"),
    ("spin", "🎡 Spin Now", "Lucky Wheel", None),
    ("nav:back", "⬅ Back buttons (every screen)", "Navigation", None),
    ("nav:back_menu", "⬅ Back to Menu (icon)", "Navigation", "danger"),
    ("cat:shop_products", "🛍️ Shop → product buttons", "Item Lists", None),
    ("shop_cat", "🗂️ Shop → Category buttons (Root/Non-Root/etc.)", "Item Lists", None),
    ("cat:shop_plans", "🛍️ Shop → plan/duration buttons", "Item Lists", None),
    ("cat:admin_product_list", "📋 Admin → product-picker lists", "Item Lists", None),
    ("cat:admin_orders", "📦 Admin → order list buttons", "Item Lists", None),
    ("cat:admin_groups", "🗂️ Admin → group-setup buttons", "Item Lists", None),
    ("cat:custom_buttons", "🔘 Admin-added custom buttons (Main Menu)", "Item Lists", None),
    ("setdel", "🔧 Manual / 🌐 API delivery-mode choice", "Item Lists", None),
]
BUTTON_REGISTRY_GROUPS = list(dict.fromkeys(g for _k, _l, g, _d in BUTTON_REGISTRY))
BUTTON_LABELS = {k: l for k, l, _g, _d in BUTTON_REGISTRY}

# callback_data values that map 1:1 to a registry key (menu:*, admin:* screens)
_STATIC_KEYS = {k for k, _l, _g, _d in BUTTON_REGISTRY}
# prefixes that need their first TWO colon-parts to stay distinct (wallet vs upi etc.)
_TWO_PART_PREFIXES = {"payplan"}
# dynamic per-item callback prefixes that should all share one category color
_CATEGORY_PREFIXES = {
    "shop_prod": "cat:shop_products",
    "shop_plan": "cat:shop_plans",
    "admrm": "cat:admin_product_list",
    "adep_prod": "cat:admin_product_list",
    "adep_plan": "cat:admin_product_list",
    "admdel": "cat:admin_product_list",
    "admicon": "cat:admin_product_list",
    "admphoto": "cat:admin_product_list",
    "addplanprod": "cat:admin_product_list",
    "vieworder": "cat:admin_orders",
    "setgroup": "cat:admin_groups",
    "custombtn": "cat:custom_buttons",
}


def clear_admin_input_state(context):
    """Cancel any pending admin input when an admin navigates away/back.

    A Back button changes the visible Telegram screen, but it does not
    automatically clear context.user_data. Clear both the pending state and
    temporary values so the next unrelated command/message can never be
    mistaken for the old edit input.
    """
    context.user_data.pop("admin_state", None)
    context.user_data.pop("admin_temp", None)


def button_style_key(callback_data, text, url=None):
    """Maps a button's callback_data/text/URL to its BUTTON_REGISTRY key."""
    # Back-to-menu is intentionally separate from other Back buttons so its
    # name/icon can be customized without changing every Back button.
    if callback_data == "menu:main" and text and text.strip().startswith("⬅"):
        return "nav:back_menu"
    if text and text.strip().startswith("⬅"):
        return "nav:back"
    text_key_map = {
        "UPDATE CHANNEL": "order:joinchannel",
        "HOW TO USE SETUP": "howto:setup",
        "CONTACT ADMIN": "support:contactadmin",
        "🛍️ Open Bot / Buy Product ↗": "purchaseproof:openbot",
        "🛍️ Open Bot / Buy Product ↗️": "purchaseproof:openbot",
    }
    if text and text.strip() in text_key_map:
        return text_key_map[text.strip()]
    if url:
        raw_url = str(url).strip()
        if raw_url.endswith("#maintenance_updates"):
            return "maintenance:checkupdates"
        normalized_url = raw_url.rstrip("/")
        saved_update = str(get_setting("update_channel", "") or "").strip().rstrip("/")
        saved_selling = str(get_setting("selling_proofs_link", "") or "").strip().rstrip("/")
        if saved_update and normalized_url == saved_update:
            return "sellingproofs:mainchannel"
        if saved_selling and normalized_url == saved_selling:
            return "sellingproofs:channel"
    if url and "?start=shop" in url and "t.me/" in url:
        return "purchaseproof:openbot"
    if not callback_data:
        return None
    if callback_data in _STATIC_KEYS:
        return callback_data
    parts = callback_data.split(":")
    prefix = parts[0]
    if prefix in _CATEGORY_PREFIXES:
        return _CATEGORY_PREFIXES[prefix]
    if prefix in _TWO_PART_PREFIXES and len(parts) >= 2:
        return f"{prefix}:{parts[1]}"
    return prefix


def payment_success_keyboard():
    """Support button shown after a successful wallet payment credit."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Support", callback_data="menu:support")
    ]])


def get_button_style(key):
    if not key:
        return None
    conn = get_conn()
    row = conn.execute("SELECT style FROM button_styles WHERE button_key = ?", (key,)).fetchone()
    conn.close()
    if row is None or row["style"] in (None, "default"):
        return None
    return row["style"]


def set_button_style(key, style):
    style = None if style in (None, "default") else style
    conn = get_conn()
    conn.execute(
        "INSERT INTO button_styles (button_key, style) VALUES (?, ?) "
        "ON CONFLICT(button_key) DO UPDATE SET style = ?",
        (key, style, style),
    )
    conn.commit()
    conn.close()


def get_button_label_override(key):
    """Returns {'label', 'icon_emoji', 'icon_custom_emoji_id'} for a button
    the admin has renamed/re-iconed, or None if it's still using its default."""
    if not key:
        return None
    conn = get_conn()
    row = conn.execute("SELECT label, icon_emoji, icon_custom_emoji_id FROM button_labels WHERE button_key = ?", (key,)).fetchone()
    conn.close()
    if row is None or (not row["label"] and not row["icon_emoji"] and not row["icon_custom_emoji_id"]):
        return None
    return dict(row)


def set_button_label_override(key, label=None, icon_emoji=None, icon_custom_emoji_id=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO button_labels (button_key, label, icon_emoji, icon_custom_emoji_id) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(button_key) DO UPDATE SET label = ?, icon_emoji = ?, icon_custom_emoji_id = ?",
        (key, label, icon_emoji, icon_custom_emoji_id, label, icon_emoji, icon_custom_emoji_id),
    )
    conn.commit()
    conn.close()


def reset_button_label_override(key):
    conn = get_conn()
    conn.execute("DELETE FROM button_labels WHERE button_key = ?", (key,))
    conn.commit()
    conn.close()


def is_button_hidden(key):
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM hidden_buttons WHERE button_key = ?", (key,)).fetchone()
    conn.close()
    return row is not None


def set_button_hidden(key, hidden):
    conn = get_conn()
    if hidden:
        conn.execute("INSERT OR IGNORE INTO hidden_buttons (button_key) VALUES (?)", (key,))
    else:
        conn.execute("DELETE FROM hidden_buttons WHERE button_key = ?", (key,))
    conn.commit()
    conn.close()


def add_custom_button(label, action_type, action_value):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO custom_buttons (label, action_type, action_value, created_at) VALUES (?, ?, ?, ?)",
        (label, action_type, action_value, int(time.time())),
    )
    conn.commit()
    cbid = cur.lastrowid
    conn.close()
    return cbid


def list_custom_buttons():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM custom_buttons ORDER BY id ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_custom_button(cbid):
    conn = get_conn()
    row = conn.execute("SELECT * FROM custom_buttons WHERE id = ?", (cbid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def remove_custom_button(cbid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM custom_buttons WHERE id = ?", (cbid,))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


# ---------------- Manage Descriptions (any admin-editable text block, anywhere) ----------------
# (key, label shown in admin panel, default template — {placeholders} get substituted at render time)
TEXT_BLOCK_REGISTRY = [
    ("welcome", "🏠 Welcome / Main Menu Text",
     "✨ — 🏪 {store_name} ✨\n\n🎉 Hello, {first_name}!\n\n🔑 Powered by {store_name}✨\n\n"
     "—  🏪  Direct deals with every supplier\n—  💧  Instant delivery after payment\n"
     "—  📤  Guaranteed discounted prices\n—  📞  24/7 admin support\n\nTap any button below to begin."),
    ("shop_panel_select", "🛒 Shop → Panel Selection Text",
     "🛒 SELECT PRODUCT PANEL\nEk panel chunein uske products dekhne ke liye:"),
    ("shop_category_header", "🛒 Shop → Product Selection (use {category} {first_name})",
     "🛒 {category}\n👤 {first_name}\n\nEk product chunein:"),
    ("shop_product_header", "🛍️ Shop → Product Price List (use {product_name} {first_name})",
     "🛍️ {product_name}\n👤 {first_name}\n\nPlan / Price chunein:"),
    ("add_balance_text", "💰 Add Balance Description (use {current_balance} {amount} {min_amount} {max_amount})",
     "💰 *ADD BALANCE*\n\nCurrent balance: ₹{current_balance}\n\nPick a quick amount below, or enter a custom amount.\nMin: ₹{min_amount} · Max: ₹{max_amount}"),
    ("order_confirmation_text", "🧾 Confirm Your Order Description (use {product_name} {duration} {price} {balance})",
     "CONFIRM YOUR ORDER\nPRODUCT: {product_name}\nDuration: {duration}\nPrice: ₹{price}\nWallet Balance: ₹{balance}"),
    ("orders_text", "📝 My Orders Description",
     "📝 *My Orders*\nYahan aapke successfully purchased orders ki history dikhegi."),
    ("mykeys_notpurchased_text", "🛍️ My Keys → Not Purchased Description",
     "🛍️ *NOT PURCHASED*\nNeeche woh products hain jo aapne abhi tak successfully purchase nahi kiye."),
    ("profile_text", "👤 Profile Description (use {user_id} {user_name} {balance} {spent} {keys_no} {joined_date} {account_type})",
     "👤 *USER PROFILE*\n\n🆔 *ID:* {user_id}\n👤 *Username:* {user_name}\n📅 *Joined:* {joined_date}\n🙋 *Account Type:* {account_type}\n\n━━━━━━━━━━━━\n\n💰 *Balance:* ₹{balance}\n💳 *Spent:* ₹{spent}\n🔑 *Keys:* {keys_no}"),
    ("reseller_text", "👑 Reseller Description (use {price} {days} {valid_until} {status})",
     "👑 *RESELLER MEMBERSHIP*\n\n*Status:* {status}\n*Price:* ₹{price} / {days} days\n*Valid Till:* {valid_until}\n\nReseller plans aur benefits yahan dekhein."),
    ("wheel_text", "🎡 Lucky Spin Description",
     "*🎡 LUCKY SPIN*\n\n*Roz ek baar spin karke wallet reward jeetein!*"),
    ("updates_text", "📣 Updates Description",
     "📣 *Updates*\nLatest updates ke liye channel join karein."),
    ("howto_text_block", "📺 How To Description",
     "📺 *How To Use*\nBot ko use karne ke steps yahan milenge."),
    ("share_text", "🎉 Share Description",
     "🎉 *Share Bot*\nApne friends ke saath bot share karein."),
    ("referral_text", "🎁 Refer & Earn Description (use {referral_code} {referred_count} {share_link})",
     "🎁 *Refer & Earn*\nYour code: `{referral_code}`\nTotal referred: {referred_count}\n\nShare link: {share_link}"),
    ("verify_text", "📱 Verify Account Description (use {first_name})",
     "🎉 Welcome, {first_name}!\n\n🔒 *VERIFICATION REQUIRED*\n\nTo start shopping, please verify by sharing your contact.\nIsse hum aapke orders track kar paate hain aur support de paate hain.\n\n👇 Neeche diya button dabakar apna contact share karein:"),
    ("support_text", "💬 Support Text",
     "💬 *Support*\nKoi bhi problem ho to yahin message karein, hum jald reply karenge."),
    ("qr_verify_pending", "⏳ Payment Verify Pending Text (use {price})",
     "⏳ ₹{price} ka payment verify ho raha hai — admin confirm karte hi product deliver ho jaayega."),
    ("order_cancelled_text", "❌ Order / QR Cancelled Description (use {first_name})",
     "❌ Order cancel ho gaya.\n\n{first_name}, agar aapko koi problem hai to Support button se contact karein."),
    ("order_expired_text", "⏰ Order / QR Expired Description (use {first_name})",
     "⏰ Order / QR expire ho gaya.\n\n{first_name}, naya QR generate karke dobara payment try karein."),
    ("purchase_proof_text", "📢 Channel Purchase Proof (use {first_name} {user_id} {product_name} {duration} {bot_username})",
     "🛍️ *NEW KEY PURCHASED!*\n\n👤 First Name: {first_name}\n🆔 User ID: {user_id}\n📦 Product: {product_name}\n⏳ Duration: {duration}\n🔑 Key: ****** (Hidden)\n\n🤖 Bot: {bot_username}"),
    ("selling_proofs_text", "🧾 Selling Proofs Description",
     "🧾 *Selling Proofs*\n\nYahan se hamare main channel aur selling proofs channel par ja sakte hain."),
    ("product_maintenance_text", "🛠️ Product Maintenance Description (use {Product_name})",
     "🛒 *{Product_name}*  ”\n\n🛠️ *NOTICE: THIS PRODUCT IS CURRENTLY UNDER MAINTENANCE.*\n\nPlease check back later or contact admin support."),
    ("delivery_receipt", "🔑 Payment Success / Delivery Receipt (use {order_id} {product_name} {plan} {quantity} {price} {license_key})",
     "🎉 PAYMENT SUCCESSFUL 🎉\n\nThank you for your purchase!\n\n"
     "📋 Order Details\n🔒 ID: {order_id}\n🔑 {product_name}\n🔑 Plan: {plan}\n📄 Quantity: {quantity}\n💰 Paid: ₹{price} INR\n\n"
     "🔑 Your Key\n{license_key}\n\nℹ️ Tap the key above to copy."),
]
TEXT_BLOCK_LABELS = {k: l for k, l, _d in TEXT_BLOCK_REGISTRY}
TEXT_BLOCK_DEFAULTS = {k: d for k, _l, d in TEXT_BLOCK_REGISTRY}


def get_text_block(key):
    """Returns (text, entities_json) — the admin's saved override if one exists,
    otherwise the built-in default template for that block."""
    conn = get_conn()
    row = conn.execute("SELECT text, entities FROM text_blocks WHERE block_key = ?", (key,)).fetchone()
    conn.close()
    if row is not None:
        # A saved empty string is also a valid admin override. This makes the
        # editor truly exact: whatever the admin saves is what users receive.
        return row["text"] or "", row["entities"] or ""
    return TEXT_BLOCK_DEFAULTS.get(key, ""), ""


def set_text_block(key, text, entities_json=""):
    conn = get_conn()
    conn.execute(
        "INSERT INTO text_blocks (block_key, text, entities) VALUES (?, ?, ?) "
        "ON CONFLICT(block_key) DO UPDATE SET text = ?, entities = ?",
        (key, text, entities_json, text, entities_json),
    )
    conn.commit()
    conn.close()


def reset_text_block(key):
    conn = get_conn()
    conn.execute("DELETE FROM text_blocks WHERE block_key = ?", (key,))
    conn.commit()
    conn.close()


def substitute_template(template, entities_raw, **kwargs):
    """Replace placeholders while keeping saved Telegram formatting aligned.

    Placeholder names are matched case-insensitively so admins can use the
    documented forms such as ``{product_name}`` or ``{Product_name}`` without
    seeing the placeholder left in the final message.
    """
    entities = json_to_entities(entities_raw) or []
    result = template
    for key, value in kwargs.items():
        token = "{" + key + "}"
        pattern = re.compile(re.escape(token), re.IGNORECASE)
        while True:
            match = pattern.search(result)
            if not match:
                break
            idx = match.start()
            matched_token = match.group(0)
            token_start = utf16_len(result[:idx])
            token_len = utf16_len(matched_token)
            value_text = str(value)
            value_len = utf16_len(value_text)
            token_end = token_start + token_len
            delta = value_len - token_len
            adjusted = []
            for e in entities:
                es, ee = e.offset, e.offset + e.length
                if ee <= token_start:
                    adjusted.append(e)
                elif es >= token_end:
                    adjusted.append(MessageEntity(type=e.type, offset=e.offset + delta, length=e.length,
                                                  url=e.url, language=e.language, custom_emoji_id=e.custom_emoji_id))
                elif es >= token_start and ee <= token_end:
                    adjusted.append(MessageEntity(type=e.type, offset=token_start,
                                                  length=value_len, url=e.url, language=e.language, custom_emoji_id=e.custom_emoji_id))
                elif es < token_start and ee > token_end:
                    adjusted.append(MessageEntity(type=e.type, offset=es,
                                                  length=e.length + delta, url=e.url, language=e.language, custom_emoji_id=e.custom_emoji_id))
                elif es < token_start < ee <= token_end:
                    adjusted.append(MessageEntity(type=e.type, offset=es,
                                                  length=token_start - es, url=e.url, language=e.language, custom_emoji_id=e.custom_emoji_id))
                elif token_start <= es < token_end < ee:
                    adjusted.append(MessageEntity(type=e.type, offset=token_start + value_len,
                                                  length=ee - token_end, url=e.url, language=e.language, custom_emoji_id=e.custom_emoji_id))
            entities = adjusted
            result = result[:idx] + value_text + result[idx + len(matched_token):]
    return result, entities

def render_text_block(key, **kwargs):
    """Render an admin-editable block while preserving exactly what was saved.

    None entities = built-in default (legacy Markdown is intentional).
    [] entities = admin override with no Telegram formatting (send literally).
    non-empty entities = admin override with Telegram formatting/custom emoji.
    """
    text, entities_raw = get_text_block(key)
    overridden = is_text_block_overridden(key)
    if overridden:
        rendered, entities = substitute_template(text, entities_raw, **kwargs)
        return rendered, (entities or [])
    rendered = text
    for k, v in kwargs.items():
        rendered = rendered.replace("{" + k + "}", str(v))
    return rendered, None


def send_kwargs_for(text_and_entities, **extra):
    """Use Telegram entities for every admin override. Never re-parse an admin
    override as Markdown, so bold/quote/underline/spoiler/custom emoji/links and
    even literal Markdown characters remain exactly as entered."""
    text, entities = text_and_entities
    kwargs = dict(extra)
    if entities is not None:
        kwargs["entities"] = entities
    else:
        kwargs["parse_mode"] = "Markdown"
    return text, kwargs


def apply_button_colors(markup):
    """Rebuild a keyboard with admin-configured color/name/visibility.

    Performance note: older code opened 3 SQLite connections *per button*
    (hidden/style/label). A main menu with 10+ buttons therefore did dozens of
    tiny DB operations before Telegram even received the response. We now load
    the three settings tables once per keyboard and apply them from memory.
    This keeps every existing admin customization intact while making normal
    navigation much faster.
    """
    if markup is None:
        return markup

    # One DB connection for the complete keyboard instead of 3 connections per
    # button. These settings are tiny tables, so loading them is inexpensive.
    conn = get_conn()
    style_rows = conn.execute("SELECT button_key, style FROM button_styles").fetchall()
    hidden_rows = conn.execute("SELECT button_key FROM hidden_buttons").fetchall()
    label_rows = conn.execute("SELECT button_key, label, icon_emoji, icon_custom_emoji_id FROM button_labels").fetchall()
    conn.close()

    styles = {r["button_key"]: r["style"] for r in style_rows}
    hidden = {r["button_key"] for r in hidden_rows}
    labels = {}
    for r in label_rows:
        if r["label"] or r["icon_emoji"] or r["icon_custom_emoji_id"]:
            labels[r["button_key"]] = dict(r)

    new_rows = []
    for row in markup.inline_keyboard:
        new_row = []
        for btn in row:
            key = button_style_key(btn.callback_data, btn.text, getattr(btn, "url", None))
            if key in hidden:
                continue
            style = styles.get(key) or "primary"
            text = btn.text
            icon_custom_emoji_id = getattr(btn, "icon_custom_emoji_id", None)
            override = labels.get(key)
            if override:
                if override.get("label"):
                    text = override["label"]
                elif override.get("icon_emoji"):
                    text = f"{override['icon_emoji']} {text}"
                if override.get("icon_custom_emoji_id") and not icon_custom_emoji_id:
                    icon_custom_emoji_id = override["icon_custom_emoji_id"]
            new_row.append(InlineKeyboardButton(
                text=text,
                callback_data=btn.callback_data,
                url=btn.url,
                icon_custom_emoji_id=icon_custom_emoji_id,
                style=style,
            ))
        if new_row:
            new_rows.append(new_row)
    return InlineKeyboardMarkup(new_rows)


def IKM(rows):
    """Drop-in replacement for InlineKeyboardMarkup(rows) that auto-applies
    the admin's saved button colors to every button in the keyboard."""
    return apply_button_colors(InlineKeyboardMarkup(rows))


async def safe_edit_message_text(query, *args, **kwargs):
    """Same as query.edit_message_text(...), but silently ignores Telegram's
    'Message is not modified' error — this fires when the new text/keyboard
    is byte-for-byte identical to what's already shown (e.g. tapping a color
    that's already selected), and isn't a real failure. Also falls back to
    plain text if Markdown parsing fails (e.g. an unescaped * or _ in a
    product name/description), so a bad character never crashes the bot.
    And if the current message is actually a photo (e.g. a UPI QR screen —
    those only have a caption, not text, so Telegram refuses to "edit text"
    on them), it deletes that photo and sends a fresh text message instead."""
    try:
        return await query.edit_message_text(*args, **kwargs)
    except BadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg:
            return None
        if "can't parse entities" in msg and kwargs.get("parse_mode"):
            kwargs = dict(kwargs)
            kwargs.pop("parse_mode", None)
            return await query.edit_message_text(*args, **kwargs)
        if "no text in the message" in msg or "message can't be edited" in msg:
            try:
                await query.delete_message()
            except Exception:
                pass
            return await safe_reply_text(query.message, *args, **kwargs)
        raise


async def safe_reply_text(target, *args, **kwargs):
    """Same as target.reply_text(...), but falls back to plain text if
    Markdown parsing fails, instead of crashing the whole bot."""
    try:
        return await target.reply_text(*args, **kwargs)
    except BadRequest as e:
        if "can't parse entities" in str(e).lower() and kwargs.get("parse_mode"):
            kwargs = dict(kwargs)
            kwargs.pop("parse_mode", None)
            return await target.reply_text(*args, **kwargs)
        raise


async def safe_send_message(bot, **kwargs):
    """Same as bot.send_message(...), but falls back to plain text if
    Markdown parsing fails, instead of crashing the whole bot."""
    try:
        return await bot.send_message(**kwargs)
    except BadRequest as e:
        if "can't parse entities" in str(e).lower() and kwargs.get("parse_mode"):
            kwargs = dict(kwargs)
            kwargs.pop("parse_mode", None)
            return await bot.send_message(**kwargs)
        raise


async def safe_send_photo(bot, **kwargs):
    """Same as bot.send_photo(...), but falls back to a plain-text caption if
    Markdown parsing fails, instead of crashing the whole bot."""
    try:
        return await bot.send_photo(**kwargs)
    except BadRequest as e:
        if "can't parse entities" in str(e).lower() and kwargs.get("parse_mode"):
            kwargs = dict(kwargs)
            kwargs.pop("parse_mode", None)
            return await bot.send_photo(**kwargs)
        raise


async def safe_edit_message_caption(query, *args, **kwargs):
    """Safely edit a Telegram caption while preserving admin-saved entities.

    python-telegram-bot uses ``caption_entities`` for captions and ``entities``
    for normal message text.  Admin text blocks are rendered with ``entities``,
    so convert it only for the caption edit; if the message has no caption and
    we fall back to text editing, convert it back to ``entities``.
    """
    kwargs = dict(kwargs)
    if "entities" in kwargs and "caption_entities" not in kwargs:
        kwargs["caption_entities"] = kwargs.pop("entities")
    try:
        return await query.edit_message_caption(*args, **kwargs)
    except BadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg:
            return None
        if "can't parse entities" in msg and kwargs.get("parse_mode"):
            retry = dict(kwargs)
            retry.pop("parse_mode", None)
            return await query.edit_message_caption(*args, **retry)
        if "there is no caption in the message" in msg:
            # Positional caption arg becomes text= for edit_message_text.
            text_kwargs = dict(kwargs)
            if "caption_entities" in text_kwargs:
                text_kwargs["entities"] = text_kwargs.pop("caption_entities")
            if args:
                text_kwargs["text"] = args[0]
                args = ()
            return await safe_edit_message_text(query, *args, **text_kwargs)
        raise


# ---------------- Performance / HTTP connection reuse ----------------

_HTTP_LOCAL = threading.local()

def get_http_session():
    """Return one requests.Session per worker thread.

    The payment/reseller APIs are called through asyncio.to_thread(), so a
    thread-local session safely reuses TCP connections without sharing a
    requests.Session across threads. This reduces connection overhead and
    keeps slow external APIs from blocking Telegram's event loop.
    """
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _HTTP_LOCAL.session = session
    return session


# ---------------- Database ----------------

def _prepare_db_storage():
    """Prepare the configured database location without overwriting data.

    When DB_PATH points to a new persistent-volume location and an older
    local store.db exists, copy it once into the new location. If the target
    already exists, never replace it. This protects existing users/balances
    during the first migration to a Railway Volume.
    """
    target = os.path.abspath(DB_PATH)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)

    legacy = os.path.join(BASE_DIR, "store.db")
    if target != os.path.abspath(legacy) and not os.path.exists(target) and os.path.isfile(legacy):
        try:
            shutil.copy2(legacy, target)
            logger.info("Migrated existing SQLite database to persistent DB path: %s", target)
        except Exception as e:
            logger.warning("Could not migrate legacy SQLite database to %s: %s", target, e)

    exists = os.path.exists(target)
    size = os.path.getsize(target) if exists else 0
    logger.info("SQLite database path: %s | exists=%s | size=%d bytes", target, exists, size)


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # Safe SQLite settings for this bot: better concurrent read/write behavior and
    # fewer transient "database is locked" errors without changing application logic.
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    _prepare_db_storage()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
        wallet_balance REAL DEFAULT 0, referral_code TEXT UNIQUE,
        referred_by INTEGER, last_spin INTEGER DEFAULT 0, created_at INTEGER)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT,
        category TEXT, stock INTEGER DEFAULT -1,
        active INTEGER DEFAULT 1, created_at INTEGER,
        delivery_mode TEXT DEFAULT 'manual', api_product_code TEXT, sort_order INTEGER DEFAULT 0)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS product_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
        label TEXT NOT NULL, price REAL NOT NULL, created_at INTEGER,
        FOREIGN KEY (product_id) REFERENCES products (id))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, product_id INTEGER,
        product_name TEXT, price REAL, status TEXT DEFAULT 'pending',
        payment_ref TEXT, created_at INTEGER)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY, added_by INTEGER, added_at INTEGER)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS topups (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL,
        status TEXT DEFAULT 'pending', created_at INTEGER)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS button_styles (
        button_key TEXT PRIMARY KEY, style TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS button_labels (
        button_key TEXT PRIMARY KEY, label TEXT, icon_emoji TEXT, icon_custom_emoji_id TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS hidden_buttons (
        button_key TEXT PRIMARY KEY)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS custom_buttons (
        id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL,
        action_type TEXT NOT NULL, action_value TEXT NOT NULL, created_at INTEGER)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS text_blocks (
        block_key TEXT PRIMARY KEY, text TEXT, entities TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS coupons (
        code TEXT PRIMARY KEY, discount_type TEXT, discount_value REAL,
        max_uses INTEGER, used_count INTEGER DEFAULT 0, active INTEGER DEFAULT 1,
        created_at INTEGER)""")
    conn.commit()

    # Seed every colorable button with its original look on first run only —
    # INSERT OR IGNORE means an admin's saved color choice is never overwritten.
    for _key, _label, _group, _default_style in BUTTON_REGISTRY:
        cur.execute("INSERT OR IGNORE INTO button_styles (button_key, style) VALUES (?, ?)", (_key, _default_style))
    conn.commit()

    # Seed default settings on first run only (won't overwrite values an admin
    # already changed via the Admin Panel, thanks to INSERT OR IGNORE).
    default_settings = {
        "upi_id": "amanofcstore@fam",
        "update_channel": "https://t.me/AMANOFCUPDATES",
        "howto_text": (
            "🛍️ Shop mein jaake apna product chunein\n"
            "⏱️ Duration/plan chunein (1 Day, 3 Days, etc.)\n"
            "📸 QR turant generate hoga — scan karke UPI se pay karein\n"
            "✅ Payment screenshot Support mein bhejein — verify hote hi product turant mil jayega!"
        ),
        "premium_price": "99",
        "premium_days": "30",
        "purchase_proof_channel": "",
        "selling_proofs_link": "",
        "selling_proofs_enabled": "1",
    }
    for key, value in default_settings.items():
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()

    # Safe migration: add columns if this DB was created before verification existed.
    existing_cols = {row["name"] for row in cur.execute("PRAGMA table_info(users)").fetchall()}
    if "verified" not in existing_cols:
        cur.execute("ALTER TABLE users ADD COLUMN verified INTEGER DEFAULT 0")
    if "phone" not in existing_cols:
        cur.execute("ALTER TABLE users ADD COLUMN phone TEXT")
    if "premium_until" not in existing_cols:
        cur.execute("ALTER TABLE users ADD COLUMN premium_until INTEGER DEFAULT 0")

    product_cols = {row["name"] for row in cur.execute("PRAGMA table_info(products)").fetchall()}
    if "delivery_mode" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN delivery_mode TEXT DEFAULT 'manual'")
    if "api_product_code" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN api_product_code TEXT")
    if "icon_emoji" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN icon_emoji TEXT")
    if "icon_custom_emoji_id" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN icon_custom_emoji_id TEXT")
    if "requires_android_id" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN requires_android_id INTEGER DEFAULT 0")
    if "description_entities" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN description_entities TEXT")
    if "photo_file_id" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN photo_file_id TEXT")
    if "channel_link" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN channel_link TEXT")
    if "maintenance" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN maintenance INTEGER DEFAULT 0")
    if "sort_order" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN sort_order INTEGER DEFAULT 0")
    if "reseller_source" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN reseller_source TEXT DEFAULT 'banti'")
    if "api_product_slug" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN api_product_slug TEXT")

    # Initialize display order for existing products without disturbing their current
    # shop order. Products are numbered separately inside each category.
    _cats = [r["category"] for r in cur.execute("SELECT DISTINCT category FROM products WHERE active = 1 AND category IS NOT NULL AND category != ''").fetchall()]
    for _cat in _cats:
        _rows = cur.execute("SELECT id FROM products WHERE active = 1 AND category = ? ORDER BY id DESC", (_cat,)).fetchall()
        _has_positive = cur.execute("SELECT 1 FROM products WHERE active = 1 AND category = ? AND sort_order > 0 LIMIT 1", (_cat,)).fetchone()
        if not _has_positive:
            for _pos, _row in enumerate(_rows, 1):
                cur.execute("UPDATE products SET sort_order = ? WHERE id = ?", (_pos, _row["id"]))

    order_cols = {row["name"] for row in cur.execute("PRAGMA table_info(orders)").fetchall()}
    if "order_ref" not in order_cols:
        cur.execute("ALTER TABLE orders ADD COLUMN order_ref TEXT")

    topup_cols = {row["name"] for row in cur.execute("PRAGMA table_info(topups)").fetchall()}
    if "zapupi_order_id" not in topup_cols:
        cur.execute("ALTER TABLE topups ADD COLUMN zapupi_order_id TEXT")
    if "unique_amount" not in topup_cols:
        cur.execute("ALTER TABLE topups ADD COLUMN unique_amount REAL")

    order_cols2 = {row["name"] for row in cur.execute("PRAGMA table_info(orders)").fetchall()}
    if "unique_amount" not in order_cols2:
        cur.execute("ALTER TABLE orders ADD COLUMN unique_amount REAL")
    if "fam_order_id" not in order_cols2:
        cur.execute("ALTER TABLE orders ADD COLUMN fam_order_id TEXT")
    if "android_id" not in order_cols2:
        cur.execute("ALTER TABLE orders ADD COLUMN android_id TEXT")
    if "delivered_key" not in order_cols2:
        cur.execute("ALTER TABLE orders ADD COLUMN delivered_key TEXT")
    if "api_status" not in order_cols2:
        cur.execute("ALTER TABLE orders ADD COLUMN api_status TEXT")
    if "delivery_time" not in order_cols2:
        cur.execute("ALTER TABLE orders ADD COLUMN delivery_time INTEGER")
    if "plan_id" not in order_cols2:
        cur.execute("ALTER TABLE orders ADD COLUMN plan_id INTEGER")

    plan_cols = {row["name"] for row in cur.execute("PRAGMA table_info(product_plans)").fetchall()}
    if "api_duration" not in plan_cols:
        cur.execute("ALTER TABLE product_plans ADD COLUMN api_duration TEXT")
    if "reseller_price" not in plan_cols:
        cur.execute("ALTER TABLE product_plans ADD COLUMN reseller_price REAL")
    if "api_duration_id" not in plan_cols:
        cur.execute("ALTER TABLE product_plans ADD COLUMN api_duration_id TEXT")
    if "api_duration_value" not in plan_cols:
        cur.execute("ALTER TABLE product_plans ADD COLUMN api_duration_value TEXT")
    if "api_duration_unit" not in plan_cols:
        cur.execute("ALTER TABLE product_plans ADD COLUMN api_duration_unit TEXT")
    if "api_price" not in plan_cols:
        cur.execute("ALTER TABLE product_plans ADD COLUMN api_price REAL")

    if "fam_order_id" not in topup_cols:
        cur.execute("ALTER TABLE topups ADD COLUMN fam_order_id TEXT")

    cur.execute("""CREATE TABLE IF NOT EXISTS processed_transactions (
        txn_ref TEXT PRIMARY KEY, processed_at INTEGER)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS category_icons (
        category TEXT PRIMARY KEY, icon_emoji TEXT, icon_custom_emoji_id TEXT, sort_order INTEGER DEFAULT 0)""")
    category_icon_cols = {row["name"] for row in cur.execute("PRAGMA table_info(category_icons)").fetchall()}
    if "sort_order" not in category_icon_cols:
        cur.execute("ALTER TABLE category_icons ADD COLUMN sort_order INTEGER DEFAULT 0")

    _category_rows = cur.execute("SELECT DISTINCT category FROM products WHERE active = 1 AND category IS NOT NULL AND category != '' ORDER BY category").fetchall()
    _missing_cats = [r["category"] for r in _category_rows if not cur.execute("SELECT 1 FROM category_icons WHERE category = ? AND sort_order > 0", (r["category"],)).fetchone()]
    if _missing_cats:
        _max_cat_order = cur.execute("SELECT COALESCE(MAX(sort_order), 0) FROM category_icons").fetchone()[0]
        for _cat in _missing_cats:
            _max_cat_order += 1
            cur.execute("INSERT INTO category_icons (category, sort_order) VALUES (?, ?) ON CONFLICT(category) DO UPDATE SET sort_order = ?", (_cat, _max_cat_order, _max_cat_order))

    # Performance indexes. CREATE INDEX IF NOT EXISTS is migration-safe and never
    # removes or changes existing data/features.
    for _index_sql in (
        "CREATE INDEX IF NOT EXISTS idx_products_category_active ON products(category, active)",
        "CREATE INDEX IF NOT EXISTS idx_product_plans_product_id ON product_plans(product_id)",
        "CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)",
        "CREATE INDEX IF NOT EXISTS idx_orders_user_status ON orders(user_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_orders_payment_ref ON orders(payment_ref)",
        "CREATE INDEX IF NOT EXISTS idx_orders_fam_order_id ON orders(fam_order_id)",
        "CREATE INDEX IF NOT EXISTS idx_orders_unique_amount ON orders(unique_amount)",
        "CREATE INDEX IF NOT EXISTS idx_topups_user_id ON topups(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_topups_status ON topups(status)",
        "CREATE INDEX IF NOT EXISTS idx_topups_user_status ON topups(user_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)",
        "CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by)",
    ):
        cur.execute(_index_sql)
    conn.commit()

    # One-time cleanup: category buttons (Root/Non-Root/etc.) used to share ONE icon
    # across all of them via the generic "shop_cat" override. Now every category can
    # have its own icon (Admin Panel → 🗂️ Set Category Icon), so clear the old shared
    # one — it would otherwise still apply as a fallback to any category without its
    # own icon set yet, which is confusing.
    cur.execute("DELETE FROM button_labels WHERE button_key = 'shop_cat'")

    # One-time cleanup: earlier versions let admins rename a "shared" button group
    # (e.g. Add Balance's ₹10/₹50/₹100 buttons, or the number-pad's 1-9 digits) with
    # one full-text label — that overwrote EVERY button in the group with the same
    # text. Those groups are icon-only now, so clear any old full-text override left
    # over from before this fix; any custom icon the admin set stays untouched.
    item_list_keys = {k for k, _l, g, _d in BUTTON_REGISTRY if g == "Item Lists"}
    if item_list_keys:
        cur.execute(
            f"UPDATE button_labels SET label = NULL WHERE button_key IN ({','.join('?' * len(item_list_keys))}) AND label IS NOT NULL",
            tuple(item_list_keys),
        )

    conn.commit()
    conn.close()


# ---------------- Settings (support contact, update channel) ----------------

def get_setting(key, default=""):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )
    conn.commit()
    conn.close()


def escape_md(text):
    """
    Escapes Telegram legacy-Markdown special characters (_ * ` [) in text that
    comes from admin-editable settings or user profile fields, so a stray
    underscore/asterisk (e.g. in a username like @AMAN_OFC) doesn't break
    parse_mode="Markdown" and silently kill the whole message.
    """
    if not text:
        return text
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


# ---------------- Custom-emoji preservation (for admin-set descriptions/text) ----------------
# Telegram custom emoji (Premium feature) aren't plain characters — they're a
# MessageEntity(type="custom_emoji", offset, length, custom_emoji_id) layered on top of
# a normal placeholder character in the text. To keep them working after we store admin
# text in the DB and resend it later, we save the entities alongside the text (as JSON)
# and resend using entities=... instead of parse_mode, since Telegram doesn't allow both
# at once. Plain Unicode emoji need none of this — they're just normal characters.

import json as _json


def utf16_len(s):
    """UTF-16 code-unit length of a string — the unit Telegram uses for every
    MessageEntity offset/length. Differs from Python's len() whenever the string
    has characters outside the Basic Multilingual Plane (most emoji, e.g. 🛒🔥📺),
    since those take 2 UTF-16 units but count as just 1 Python character."""
    return len(s.encode("utf-16-le")) // 2


def extract_message_entities(entities, sub_start=None, sub_end=None, custom_only=False):
    """Store Telegram message formatting entities so admin-edited text keeps
    bold/italic/underline/strike/spoiler/code/blockquote/custom emoji and links.
    Offsets are UTF-16 units, exactly as Telegram sends them."""
    if not entities:
        return []
    out = []
    for e in entities:
        if custom_only and e.type != "custom_emoji":
            continue
        start, end = e.offset, e.offset + e.length
        if sub_start is not None:
            if start < sub_start or end > sub_end:
                continue
            start -= sub_start
        item = {"type": e.type, "offset": start, "length": e.length}
        for attr in ("url", "language", "custom_emoji_id"):
            val = getattr(e, attr, None)
            if val:
                item[attr] = val
        out.append(item)
    return out


def extract_custom_emoji_entities(entities, sub_start=None, sub_end=None):
    return extract_message_entities(entities, sub_start, sub_end, custom_only=True)


def entities_to_json(entities_list):
    return _json.dumps(entities_list, ensure_ascii=False) if entities_list else ""


def json_to_entities(raw_json):
    """Rebuild Telegram MessageEntity objects from stored JSON."""
    if not raw_json:
        return None
    try:
        items = _json.loads(raw_json)
    except (ValueError, TypeError):
        return None
    if not items:
        return None
    result = []
    for it in items:
        kwargs = {"type": it.get("type", "custom_emoji"),
                  "offset": int(it.get("offset", 0)),
                  "length": int(it.get("length", 0))}
        for attr in ("url", "language", "custom_emoji_id"):
            if it.get(attr):
                kwargs[attr] = it[attr]
        result.append(MessageEntity(**kwargs))
    return result


def split_with_offsets(text, delimiter="|"):
    """Splits text like str.split(delimiter) but also returns each piece's (start, end)
    UTF-16 offsets in the ORIGINAL text, so entity offsets (also UTF-16-based) line up."""
    segments = []
    offset = 0
    for raw in text.split(delimiter):
        start = offset
        end = offset + utf16_len(raw)
        segments.append((start, end, raw))
        offset = end + 1  # +1 skips the delimiter character (always 1 UTF-16 unit)
    return segments


# ---------------- Admins (dynamic, on top of ADMIN_IDS in config) ----------------

def add_admin_db(user_id, added_by):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO admins (user_id, added_by, added_at) VALUES (?, ?, ?)", (user_id, added_by, int(time.time())))
    conn.commit()
    conn.close()


def list_admins_db():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM admins ORDER BY added_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def remove_admin_db(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


def is_admin_db(user_id):
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row is not None


def get_all_admin_ids():
    return list(set(ADMIN_IDS) | {a["user_id"] for a in list_admins_db()})


def create_topup(user_id, amount):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO topups (user_id, amount, status, created_at) VALUES (?, ?, 'pending', ?)", (user_id, amount, int(time.time())))
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return tid


def get_topup(topup_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM topups WHERE id = ?", (topup_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_topup_zapupi_order(topup_id, zap_order_id):
    conn = get_conn()
    conn.execute("UPDATE topups SET zapupi_order_id = ? WHERE id = ?", (zap_order_id, topup_id))
    conn.commit()
    conn.close()


def set_topup_unique_amount(topup_id, amount):
    conn = get_conn()
    conn.execute("UPDATE topups SET unique_amount = ? WHERE id = ?", (amount, topup_id))
    conn.commit()
    conn.close()


def set_order_unique_amount(order_id, amount):
    conn = get_conn()
    conn.execute("UPDATE orders SET unique_amount = ? WHERE id = ?", (amount, order_id))
    conn.commit()
    conn.close()


def find_pending_topup_by_amount(amount):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM topups WHERE status = 'pending' AND unique_amount IS NOT NULL "
        "AND ABS(unique_amount - ?) < 0.005 ORDER BY created_at DESC LIMIT 1", (amount,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def find_pending_order_by_amount(amount):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM orders WHERE status = 'pending' AND unique_amount IS NOT NULL "
        "AND ABS(unique_amount - ?) < 0.005 ORDER BY created_at DESC LIMIT 1", (amount,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_topup_fam_order(topup_id, fam_order_id):
    conn = get_conn()
    conn.execute("UPDATE topups SET fam_order_id = ? WHERE id = ?", (fam_order_id, topup_id))
    conn.commit()
    conn.close()


def set_order_fam_order(order_id, fam_order_id):
    conn = get_conn()
    conn.execute("UPDATE orders SET fam_order_id = ? WHERE id = ?", (fam_order_id, order_id))
    conn.commit()
    conn.close()


def set_order_android_id(order_id, android_id):
    conn = get_conn()
    conn.execute("UPDATE orders SET android_id = ? WHERE id = ?", (android_id, order_id))
    conn.commit()
    conn.close()


def set_order_delivery_info(order_id, delivered_key=None, api_status=None):
    """Records what was delivered (API key or manual text) and how, for admin
    visibility and duplicate-delivery protection — call this once per order."""
    conn = get_conn()
    conn.execute(
        "UPDATE orders SET delivered_key = ?, api_status = ?, delivery_time = ? WHERE id = ?",
        (delivered_key, api_status, int(time.time()), order_id),
    )
    conn.commit()
    conn.close()


def is_transaction_processed(txn_ref):
    """Duplicate-payment guard: true if this exact txn_id/UTR (or fam_order_id, as a
    fallback when the API doesn't return one) has already triggered a credit/delivery."""
    if not txn_ref:
        return False
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM processed_transactions WHERE txn_ref = ?", (str(txn_ref),)).fetchone()
    conn.close()
    return row is not None


def mark_transaction_processed(txn_ref):
    if not txn_ref:
        return
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO processed_transactions (txn_ref, processed_at) VALUES (?, ?)", (str(txn_ref), int(time.time())))
    conn.commit()
    conn.close()


def set_topup_status(topup_id, status):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE topups SET status = ? WHERE id = ?", (status, topup_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


def get_or_create_user(user_id, username, first_name, referred_by=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row:
        conn.close()
        return dict(row)
    ref_code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    cur.execute(
        "INSERT INTO users (user_id, username, first_name, wallet_balance, referral_code, referred_by, last_spin, created_at) "
        "VALUES (?, ?, ?, 0, ?, ?, 0, ?)",
        (user_id, username, first_name, ref_code, referred_by, int(time.time())),
    )
    conn.commit()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = dict(cur.fetchone())
    conn.close()
    return row


def get_user_row(user_id):
    """Return the existing user row as a dict, or None."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_verified(user_id, phone):
    conn = get_conn()
    conn.execute("UPDATE users SET verified = 1, phone = ? WHERE user_id = ?", (phone, user_id))
    conn.commit()
    conn.close()


def list_all_user_ids():
    conn = get_conn()
    rows = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def monthly_member_count():
    """Cumulative unique users stored by the bot; no monthly/30-day reset."""
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
    conn.close()
    return int(row["c"] or 0)


def is_verified(user_id):
    conn = get_conn()
    row = conn.execute("SELECT verified FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return bool(row and row["verified"])


def get_premium_until(user_id):
    conn = get_conn()
    row = conn.execute("SELECT premium_until FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row["premium_until"] if row else 0


def is_premium(user_id):
    return get_premium_until(user_id) > int(time.time())


def extend_premium(user_id, days):
    current = get_premium_until(user_id)
    now = int(time.time())
    base = current if current > now else now  # extend from expiry if still active, else from now
    new_until = base + days * 86400
    conn = get_conn()
    conn.execute("UPDATE users SET premium_until = ? WHERE user_id = ?", (new_until, user_id))
    conn.commit()
    conn.close()
    return new_until


def revoke_premium(user_id):
    conn = get_conn()
    conn.execute("UPDATE users SET premium_until = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def compute_final_price(user_id, plan):
    """Resellers (Premium) pay the admin-fixed reseller_price for this specific
    plan, if one's been set — a flat price the admin chose, not a percentage
    discount. Returns (final_price, note_text)."""
    if is_premium(user_id) and plan.get("reseller_price") is not None:
        final_price = plan["reseller_price"]
        if final_price != plan["price"]:
            note = f"👑 Reseller price laga hua hai: original ₹{plan['price']:.0f} → ₹{final_price:.0f}\n"
            return final_price, note
    return plan["price"], ""


# ---------------- Coupon Codes ----------------

def create_coupon(code, discount_type, discount_value, max_uses=None):
    code = code.strip().upper()
    conn = get_conn()
    existing = conn.execute("SELECT used_count FROM coupons WHERE code = ?", (code,)).fetchone()
    used_count = existing["used_count"] if existing else 0
    conn.execute(
        "INSERT OR REPLACE INTO coupons (code, discount_type, discount_value, max_uses, used_count, active, created_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?)",
        (code, discount_type, discount_value, max_uses, used_count, int(time.time())),
    )
    conn.commit()
    conn.close()
    return code


def get_coupon(code):
    if not code:
        return None
    conn = get_conn()
    row = conn.execute("SELECT * FROM coupons WHERE code = ?", (code.strip().upper(),)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_coupons():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM coupons ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_coupon(code):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM coupons WHERE code = ?", (code.strip().upper(),))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def increment_coupon_usage(code):
    conn = get_conn()
    conn.execute("UPDATE coupons SET used_count = used_count + 1 WHERE code = ?", (code.strip().upper(),))
    conn.commit()
    conn.close()


def coupon_is_valid(coupon):
    """Returns (is_valid, error_message_or_None)."""
    if not coupon:
        return False, "❌ Ye coupon code exist nahi karta."
    if not coupon["active"]:
        return False, "❌ Ye coupon deactivate ho chuka hai."
    if coupon["max_uses"] is not None and coupon["used_count"] >= coupon["max_uses"]:
        return False, "❌ Ye coupon ki usage limit khatam ho chuki hai."
    return True, None


def apply_coupon_discount(price, coupon):
    """Returns (final_price, discount_amount)."""
    if coupon["discount_type"] == "percent":
        discount = price * (coupon["discount_value"] / 100)
    else:
        discount = coupon["discount_value"]
    discount = max(0, min(discount, price))
    final_price = round(price - discount, 2)
    return final_price, round(discount, 2)


def contact_admin_row():
    """Returns a single-button row with the 'Contact Admin' link (same one shown
    in the Support menu), or an empty list if no admin username is configured."""
    admin_username = get_setting("admin_contact_username", "")
    if admin_username:
        return [[InlineKeyboardButton("CONTACT ADMIN", url=f"https://t.me/{admin_username}")]]
    return []


def topup_min_max():
    return float(get_setting("topup_min", "1")), float(get_setting("topup_max", "50000"))


def topup_amount_buttons():
    return [
        [InlineKeyboardButton("₹100", callback_data="topup:100"),
         InlineKeyboardButton("₹500", callback_data="topup:500")],
        [InlineKeyboardButton("₹1000", callback_data="topup:1000"),
         InlineKeyboardButton("₹2000", callback_data="topup:2000")],
        [InlineKeyboardButton("✏️ Custom Amount", callback_data="topup:custom")],
        [InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")],
    ]


def topup_keypad_text(amount_str, balance):
    """Render the editable Add Balance description with live values."""
    display = amount_str if amount_str else "0"
    min_amount, max_amount = topup_min_max()
    return render_text_block(
        "add_balance_text",
        amount=display,
        current_balance=f"{float(balance):.2f}",
        # Keep the old {balance} placeholder working for already-saved admin text.
        balance=f"{float(balance):.2f}",
        min_amount=f"{min_amount:.2f}",
        max_amount=f"{max_amount:.2f}",
    )


def _apply_topup_dynamic_button_labels(markup, amount_str, balance):
    """Resolve optional {amount}/{balance} placeholders on Add Balance buttons."""
    if markup is None:
        return markup
    amount = amount_str if amount_str else "0"
    values = {"amount": amount, "balance": f"{float(balance):.2f}"}
    new_rows = []
    override = get_button_label_override("kpadconfirm")
    for row in markup.inline_keyboard:
        new_row = []
        for btn in row:
            text = btn.text
            if btn.callback_data == "kpadconfirm" and override and override.get("label"):
                text = override["label"]
                for name, value in values.items():
                    text = re.sub(r"\{" + re.escape(name) + r"\}", str(value), text, flags=re.IGNORECASE)
            new_row.append(InlineKeyboardButton(
                text=text, callback_data=btn.callback_data, url=btn.url,
                icon_custom_emoji_id=getattr(btn, "icon_custom_emoji_id", None),
                style=getattr(btn, "style", None),
            ))
        new_rows.append(new_row)
    return InlineKeyboardMarkup(new_rows)


def topup_keypad_keyboard(amount_str, balance):
    display = amount_str if amount_str else "0"
    rows = [
        [InlineKeyboardButton("1", callback_data="kpadnum:1"), InlineKeyboardButton("2", callback_data="kpadnum:2"), InlineKeyboardButton("3", callback_data="kpadnum:3")],
        [InlineKeyboardButton("4", callback_data="kpadnum:4"), InlineKeyboardButton("5", callback_data="kpadnum:5"), InlineKeyboardButton("6", callback_data="kpadnum:6")],
        [InlineKeyboardButton("7", callback_data="kpadnum:7"), InlineKeyboardButton("8", callback_data="kpadnum:8"), InlineKeyboardButton("9", callback_data="kpadnum:9")],
        [InlineKeyboardButton("C", callback_data="kpadclear"),
         InlineKeyboardButton("0", callback_data="kpadnum:0"), InlineKeyboardButton("⌫", callback_data="kpadbksp")],
        [InlineKeyboardButton("✅ Confirm ₹" + display, callback_data="kpadconfirm")],
        [InlineKeyboardButton("✋ Back", callback_data="kpadback")],
    ]
    return _apply_topup_dynamic_button_labels(IKM(rows), amount_str, balance)


def build_plan_payment_screen(user, plan, product, context):
    """Build the final order-confirmation/payment screen.

    The order details are intentionally shown as a Telegram blockquote and use
    the exact product name, exact plan label shown on the duration button, the
    effective price after Premium/coupon discounts, and the user's live wallet
    balance.
    """
    plan_id = plan["id"]
    final_price, discount_note = compute_final_price(user.id, plan)
    applied_coupons = context.user_data.setdefault("applied_coupons", {})
    applied_code = applied_coupons.get(str(plan_id))
    if applied_code:
        coupon = get_coupon(applied_code)
        valid, _err = coupon_is_valid(coupon)
        if valid:
            final_price, _discount_amt = apply_coupon_discount(final_price, coupon)
        else:
            applied_coupons.pop(str(plan_id), None)
            applied_code = None

    row = get_or_create_user(user.id, user.username, user.first_name)
    duration_label = str(plan.get("label") or "")
    product_name = str(product.get("name") or "Product")

    # The complete confirmation text is admin-editable.  The four placeholders
    # are substituted at tap-time so the admin can freely change wording,
    # line order, emojis, Premium/custom emoji, links, bold/italic, and quote
    # formatting without losing the live order values.
    text, entities = render_text_block(
        "order_confirmation_text",
        product_name=product_name,
        duration=duration_label,
        price=f"{final_price:.2f}",
        balance=f"{row['wallet_balance']:.2f}",
    )
    # Keep the built-in/default confirmation visually quoted like the existing
    # screen.  Once the admin saves an override, render_text_block returns the
    # exact Telegram entities that were saved (including custom emoji/quote).
    if entities is None:
        entities = [MessageEntity(type="blockquote", offset=0, length=utf16_len(text))]

    buttons = []
    if row["wallet_balance"] >= final_price:
        buttons.append([InlineKeyboardButton(
            f"Pay ₹{final_price:.2f} via Wallet",
            callback_data=f"payplan:wallet:{plan_id}",
            style="success",
        )])
    buttons.append([InlineKeyboardButton(
        f"Pay ₹{final_price:.2f} via UPI",
        callback_data=f"payplan:upi:{plan_id}",
        style="primary",
    )])
    if applied_code:
        buttons.append([InlineKeyboardButton(
            f"Remove Coupon ({applied_code})",
            callback_data=f"removecoupon:{plan_id}",
        )])
    else:
        buttons.append([InlineKeyboardButton(
            "Apply Coupon Code",
            callback_data=f"applycoupon:{plan_id}",
        )])
    # Returning from the confirmation screen should reopen this exact
    # product's price list, not the category/panel selector.
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data=f"shop_prod:{product['id']}")])
    return text, IKM(buttons), entities


def add_wallet_balance(user_id, amount):
    conn = get_conn()
    conn.execute("UPDATE users SET wallet_balance = wallet_balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()


def deduct_wallet_balance(user_id, amount):
    conn = get_conn()
    row = conn.execute("SELECT wallet_balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row or row["wallet_balance"] < amount:
        conn.close()
        return False
    conn.execute("UPDATE users SET wallet_balance = wallet_balance - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()
    return True


def set_last_spin(user_id, ts):
    conn = get_conn()
    conn.execute("UPDATE users SET last_spin = ? WHERE user_id = ?", (ts, user_id))
    conn.commit()
    conn.close()


def get_referral_count(user_id):
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) as c FROM users WHERE referred_by = ?", (user_id,)).fetchone()
    conn.close()
    return row["c"]


def add_product(name, description, category="General", description_entities="", reseller_source="banti", api_product_id=None, api_product_slug=None):
    conn = get_conn()
    cur = conn.cursor()
    next_order = cur.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM products WHERE active = 1 AND category = ?",
        (category or "General",),
    ).fetchone()["n"]
    cur.execute(
        "INSERT INTO products (name, description, category, active, created_at, delivery_mode, description_entities, sort_order, reseller_source, api_product_code, api_product_slug) VALUES (?, ?, ?, 1, ?, 'manual', ?, ?, ?, ?, ?)",
        (name, description, category, int(time.time()), description_entities, next_order, reseller_source or "banti", str(api_product_id) if api_product_id is not None else None, api_product_slug),
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def set_product_description(product_id, description, description_entities=""):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE products SET description = ?, description_entities = ? WHERE id = ?",
        (description, description_entities, product_id),
    )
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


def set_product_delivery(product_id, mode, api_product_code=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE products SET delivery_mode = ?, api_product_code = ? WHERE id = ?",
        (mode, api_product_code, product_id),
    )
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


def set_requires_android_id(product_id, requires):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE products SET requires_android_id = ? WHERE id = ?", (1 if requires else 0, product_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


def set_product_icon(product_id, icon_emoji=None, icon_custom_emoji_id=None):
    """Sets either a plain unicode emoji (icon_emoji) or a custom-emoji sticker id
    (icon_custom_emoji_id, requires the bot owner's Telegram Premium) as the product's icon."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE products SET icon_emoji = ?, icon_custom_emoji_id = ? WHERE id = ?",
        (icon_emoji, icon_custom_emoji_id, product_id),
    )
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


def set_product_photo(product_id, file_id):
    """Saves the Telegram file_id of a product's photo, shown above its price list in the shop."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE products SET photo_file_id = ? WHERE id = ?", (file_id, product_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


def get_product_order(product_id):
    conn = get_conn()
    row = conn.execute("SELECT sort_order FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    return int(row["sort_order"] or 0) if row else 0


def set_product_order(product_id, new_position):
    """Move one product to a 1-based position within its category and shift the
    other products automatically so positions stay unique and contiguous."""
    try:
        new_position = int(new_position)
    except (TypeError, ValueError):
        return False
    conn = get_conn()
    cur = conn.cursor()
    product = cur.execute("SELECT id, category FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        conn.close(); return False
    category = product["category"] or "General"
    rows = cur.execute("SELECT id FROM products WHERE active = 1 AND category = ? ORDER BY sort_order ASC, id DESC", (category,)).fetchall()
    ids = [r["id"] for r in rows]
    if product_id not in ids:
        conn.close(); return False
    new_position = max(1, min(new_position, len(ids)))
    ids.remove(product_id)
    ids.insert(new_position - 1, product_id)
    for pos, pid in enumerate(ids, 1):
        cur.execute("UPDATE products SET sort_order = ? WHERE id = ?", (pos, pid))
    conn.commit(); conn.close(); return True


def set_category_order(category, new_position):
    """Move a category to a 1-based global position and shift other categories."""
    try:
        new_position = int(new_position)
    except (TypeError, ValueError):
        return False
    category = str(category or "").strip()
    if not category:
        return False
    categories = list_categories()
    if category not in categories:
        return False
    new_position = max(1, min(new_position, len(categories)))
    categories.remove(category)
    categories.insert(new_position - 1, category)
    conn = get_conn(); cur = conn.cursor()
    for pos, cat in enumerate(categories, 1):
        cur.execute(
            "INSERT INTO category_icons (category, sort_order) VALUES (?, ?) "
            "ON CONFLICT(category) DO UPDATE SET sort_order = ?",
            (cat, pos, pos),
        )
    conn.commit(); conn.close(); return True


def set_product_maintenance(product_id, enabled=True):
    """Turns maintenance mode on/off for one product without disabling the product itself."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE products SET maintenance = ? WHERE id = ?", (1 if enabled else 0, product_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


def set_product_channel(product_id, channel_link):
    """Saves a separate update/channel URL for this product."""
    clean = (channel_link or "").strip()
    if clean.lower() in ("off", "remove", "none", "-"):
        clean = ""
    if clean.startswith("@"): 
        clean = f"https://t.me/{clean[1:]}"
    elif clean.startswith("t.me/"):
        clean = f"https://{clean}"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE products SET channel_link = ? WHERE id = ?", (clean, product_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


def add_plan(product_id, label, price):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO product_plans (product_id, label, price, created_at) VALUES (?, ?, ?, ?)",
        (product_id, label, price, int(time.time())),
    )
    conn.commit()
    plan_id = cur.lastrowid
    conn.close()
    return plan_id


def add_aditya_plan(product_id, label, price, duration_id, duration_value, duration_unit, api_price=None):
    """Add one Aditya duration to an existing local product.

    ``price`` is the customer-facing selling price, while ``api_price`` is the
    Aditya cost price.  Keeping both values lets the admin add a markup without
    exposing the reseller's cost to users.
    """
    if api_price is None:
        api_price = price
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO product_plans (product_id, label, price, created_at, api_duration, api_duration_id, api_duration_value, api_duration_unit, api_price) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (product_id, label, float(price), int(time.time()), f"{duration_value} {duration_unit}".strip(), str(duration_id), str(duration_value), str(duration_unit), float(api_price)),
    )
    conn.commit()
    plan_id = cur.lastrowid
    conn.close()
    return plan_id


def get_aditya_product_by_remote(product_id=None, slug=None):
    """Find the existing local Aditya product for a remote product.

    Remote Product ID is preferred; slug is the fallback for APIs that do not
    provide a stable numeric/string ID in every response. This prevents each
    Aditya duration from creating a duplicate local product.
    """
    conn = get_conn()
    row = None
    if product_id is not None:
        row = conn.execute(
            "SELECT * FROM products WHERE reseller_source = 'aditya' AND api_product_code = ? ORDER BY id ASC LIMIT 1",
            (str(product_id),),
        ).fetchone()
    if not row and slug:
        row = conn.execute(
            "SELECT * FROM products WHERE reseller_source = 'aditya' AND api_product_slug = ? ORDER BY id ASC LIMIT 1",
            (str(slug),),
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_aditya_plan_by_duration(product_id, duration_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM product_plans WHERE product_id = ? AND api_duration_id = ? LIMIT 1",
        (product_id, str(duration_id)),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_aditya_plan(plan_id, price, duration_value, duration_unit, api_price=None):
    """Update an existing Aditya duration without changing its local plan ID."""
    if api_price is None:
        api_price = price
    label = f"{duration_value} {duration_unit}".strip()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE product_plans SET label = ?, price = ?, api_duration = ?, api_duration_value = ?, api_duration_unit = ?, api_price = ? WHERE id = ?",
        (label, float(price), label, str(duration_value), str(duration_unit), float(api_price), plan_id),
    )
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


def set_product_reseller_source(product_id, source, api_product_id=None, api_product_slug=None):
    conn = get_conn()
    conn.execute(
        "UPDATE products SET reseller_source = ?, api_product_code = COALESCE(?, api_product_code), api_product_slug = COALESCE(?, api_product_slug) WHERE id = ?",
        (source or "banti", str(api_product_id) if api_product_id is not None else None, api_product_slug, product_id),
    )
    conn.commit(); conn.close()


def get_plans(product_id):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM product_plans WHERE product_id = ? ORDER BY price ASC", (product_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_plan(plan_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM product_plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_plan_price(plan_id, new_price):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE product_plans SET price = ? WHERE id = ?", (new_price, plan_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


def remove_product(product_id):
    """Permanently deletes a product and its plans — not a soft-deactivate.
    Existing order records keep their own product_name/price snapshot, so past
    orders and delivery history stay intact even after the product is gone."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM product_plans WHERE product_id = ?", (product_id,))
    cur.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


def list_products(active_only=True):
    conn = get_conn()
    if active_only:
        rows = conn.execute("SELECT * FROM products WHERE active = 1 ORDER BY CASE WHEN sort_order > 0 THEN 0 ELSE 1 END, sort_order ASC, id DESC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM products ORDER BY CASE WHEN sort_order > 0 THEN 0 ELSE 1 END, sort_order ASC, id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_categories():
    conn = get_conn()
    rows = conn.execute(
        "SELECT p.category, COALESCE(ci.sort_order, 0) AS category_order "
        "FROM products p LEFT JOIN category_icons ci ON ci.category = p.category "
        "WHERE p.active = 1 AND p.category IS NOT NULL AND p.category != '' "
        "GROUP BY p.category ORDER BY CASE WHEN COALESCE(ci.sort_order, 0) > 0 THEN 0 ELSE 1 END, category_order ASC, p.category ASC"
    ).fetchall()
    conn.close()
    return [r["category"] for r in rows]


def get_category_icon(category):
    conn = get_conn()
    row = conn.execute("SELECT icon_emoji, icon_custom_emoji_id FROM category_icons WHERE category = ?", (category,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_category_icon(category, icon_emoji=None, icon_custom_emoji_id=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO category_icons (category, icon_emoji, icon_custom_emoji_id, sort_order) "
        "VALUES (?, ?, ?, COALESCE((SELECT MAX(sort_order) + 1 FROM category_icons), 1)) "
        "ON CONFLICT(category) DO UPDATE SET icon_emoji = ?, icon_custom_emoji_id = ?",
        (category, icon_emoji, icon_custom_emoji_id, icon_emoji, icon_custom_emoji_id),
    )
    conn.commit()
    conn.close()


def list_products_by_category(category):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM products WHERE active = 1 AND category = ? ORDER BY CASE WHEN sort_order > 0 THEN 0 ELSE 1 END, sort_order ASC, id DESC", (category,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_product(product_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_order(user_id, product_id, product_name, price, status="pending", plan_id=None):
    order_ref = "ORD" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO orders (user_id, product_id, product_name, price, status, created_at, order_ref, plan_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, product_id, product_name, price, status, int(time.time()), order_ref, plan_id),
    )
    conn.commit()
    oid = cur.lastrowid
    conn.close()
    return oid


def set_plan_api_duration(plan_id, api_duration):
    conn = get_conn()
    conn.execute("UPDATE product_plans SET api_duration = ? WHERE id = ?", (api_duration, plan_id))
    conn.commit()
    conn.close()


def set_plan_reseller_price(plan_id, price):
    """price=None clears the override, so the plan falls back to its normal price for resellers too."""
    conn = get_conn()
    conn.execute("UPDATE product_plans SET reseller_price = ? WHERE id = ?", (price, plan_id))
    conn.commit()
    conn.close()


def get_order_api_duration(order):
    """The exact duration string to send to the reseller API for this order —
    prefers the plan's admin-set 'api_duration' (which can differ from the
    customer-facing label, since the reseller panel may expect exact different
    spelling/casing), and falls back to parsing it out of the product name for
    older orders placed before this field existed."""
    plan = get_plan(order["plan_id"]) if order.get("plan_id") else None
    if plan and plan.get("api_duration"):
        return plan["api_duration"]
    return extract_duration(order["product_name"])


def format_delivery_receipt(order, key_text):
    """Builds the 'PAYMENT SUCCESSFUL' receipt shown to the customer after key
    delivery. Returns (text, entities) — entities is None for the plain default
    (send with parse_mode="Markdown"), or a list if the admin customized it with
    custom/premium emoji (send with entities=... instead)."""
    full_name = order["product_name"]
    plan_label = ""
    base_name = full_name
    if "(" in full_name and full_name.endswith(")"):
        base_name, _, rest = full_name.partition("(")
        base_name = base_name.strip()
        plan_label = rest.rstrip(")").strip()

    return render_text_block("delivery_receipt",
        order_id=order.get("order_ref") or ("ORD" + str(order["id"])),
        product_name=base_name,
        plan=plan_label or "-",
        quantity="1",
        price=f"{order['price']:.2f}",
        license_key=key_text,
    )


def delivery_receipt_keyboard(order=None):
    """Keyboard under a delivered key. Prefer that product's own channel; fall back
    to the global update channel for old products that have no per-product channel."""
    channel = ""
    if order and order.get("product_id"):
        product = get_product(order["product_id"])
        if product:
            channel = product.get("channel_link") or ""
    if not channel:
        channel = get_setting("update_channel", "")
    buttons = []
    if channel:
        url = channel if channel.startswith("http") else f"https://t.me/{channel.lstrip('@')}"
        buttons.append([InlineKeyboardButton("UPDATE CHANNEL", url=url)])
    buttons.append([InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")])
    return IKM(buttons)


def normalize_channel_target(value):
    """Convert common Telegram channel inputs into a sendable chat target."""
    value = (value or "").strip()
    if not value or value.lower() in ("off", "remove", "none", "-"):
        return ""
    if value.startswith("https://t.me/"):
        tail = value.split("https://t.me/", 1)[1].strip("/")
        if tail.startswith("+") or tail.startswith("joinchat/"):
            return value  # invite links cannot be used as chat_id; Telegram will reject cleanly
        return "@" + tail.split("/", 1)[0].lstrip("@")
    if value.startswith("http://t.me/"):
        tail = value.split("http://t.me/", 1)[1].strip("/")
        return "@" + tail.split("/", 1)[0].lstrip("@")
    return value


async def post_purchase_proof_channel(context: ContextTypes.DEFAULT_TYPE, order):
    """Automatically post a hidden-key purchase proof to the configured channel.

    The actual license key is never included in the public proof. The post uses
    the same admin-editable text/entity system as the other descriptions and a
    direct bot-start button so users can open the shop immediately.
    """
    target = normalize_channel_target(get_setting("purchase_proof_channel", ""))
    if not target:
        return

    user_row = get_user_row(order.get("user_id")) or {}
    first_name = user_row.get("first_name") or "User"
    product = get_product(order.get("product_id")) if order.get("product_id") else None
    product_name = product.get("name") if product else (order.get("product_name") or "Product")

    duration = "-"
    if order.get("plan_id"):
        plan = get_plan(order["plan_id"])
        if plan and plan.get("label"):
            duration = str(plan["label"])
    if duration == "-":
        full_name = str(order.get("product_name") or "")
        if "(" in full_name and full_name.endswith(")"):
            duration = full_name.rsplit("(", 1)[1].rstrip(")").strip() or "-"

    text, entities = render_text_block(
        "purchase_proof_text",
        first_name=first_name,
        user_id=order.get("user_id") or "",
        product_name=product_name,
        duration=duration,
        bot_username=get_bot_username(),
    )
    kwargs = {"reply_markup": IKM([[InlineKeyboardButton("🛍️ Open Bot / Buy Product ↗️", url=f"https://t.me/{get_bot_username().lstrip('@')}?start=shop")]])}
    if entities is not None:
        # Telegram custom/premium emoji are MessageEntity objects. Send the
        # saved entities directly; never run an admin-edited proof through
        # Markdown/HTML parsing, which would strip custom-emoji entities.
        kwargs["entities"] = list(entities)
        custom_count = sum(1 for e in entities if getattr(e, "type", "") == "custom_emoji")
        logger.info("Purchase proof #%s: sending %d Telegram entities (%d custom emoji)", order.get("id"), len(entities), custom_count)
    else:
        kwargs["parse_mode"] = "Markdown"
    try:
        await safe_send_message(context.bot, chat_id=target, text=text, **kwargs)
    except Exception as e:
        logger.warning(f"Could not post purchase proof to channel {target}: {e}")


async def post_key_to_log_group(context: ContextTypes.DEFAULT_TYPE, order, key_text):
    """Posts the exact key/text just delivered to a customer into the Key Details
    Log group (set via /groupid → 🔑 Use as Key Details Log) — only the key itself,
    same as what the buyer received, no order/buyer metadata."""
    text, entities = format_delivery_receipt(order, key_text)
    await notify_group(context, get_log_group("keys"), text, entities=entities)
    await post_purchase_proof_channel(context, order)


async def try_auto_deliver_order(context: ContextTypes.DEFAULT_TYPE, order_id: int) -> bool:
    """Called right after a payment auto-verifies. If the product is on API
    delivery mode (and doesn't need an Android ID we don't have yet), buys the
    key from the reseller and sends it to the customer immediately — no admin
    tap needed. Returns True if it fully auto-delivered; False means the normal
    'notify admin' fallback should run instead (manual-mode products, or an
    API failure — nothing is lost, the order just stays pending for the admin)."""
    order = get_order(order_id)
    if not order or order["status"] != "pending":
        logger.info(f"try_auto_deliver_order #{order_id}: skipped — order missing or not pending (status={order['status'] if order else 'N/A'})")
        return False
    product = get_product(order["product_id"])
    if not product or product.get("delivery_mode") != "api" or not product.get("api_product_code"):
        logger.info(f"try_auto_deliver_order #{order_id}: skipped — delivery_mode={product.get('delivery_mode') if product else 'N/A'}, api_product_code={product.get('api_product_code') if product else 'N/A'} (needs delivery_mode='api' + a PID set via Admin Panel → Set Delivery Mode)")
        return False
    if product.get("requires_android_id") and not order.get("android_id"):
        logger.info(f"try_auto_deliver_order #{order_id}: skipped — product requires Android ID, none on file yet")
        return False  # needs a human to collect the Android ID from the customer first

    duration = get_order_api_duration(order)
    result = await buy_key_for_product(product, order, order_id)
    if not result.get("success"):
        logger.warning(f"Auto-deliver failed for order #{order_id}, admin will need to deliver manually — reason: {result.get('error')}")
        return False

    key_text = result["key"]
    set_order_status(order_id, "delivered")
    set_order_delivery_info(order_id, delivered_key=key_text, api_status="success")
    try:
        receipt_text, receipt_kwargs = send_kwargs_for(format_delivery_receipt(order, key_text), reply_markup=delivery_receipt_keyboard(order))
        await safe_send_message(context.bot, chat_id=order["user_id"], text=receipt_text, **receipt_kwargs)
    except Exception as e:
        logger.warning(f"Order #{order_id} delivered via API but couldn't message the buyer: {e}")
    await post_key_to_log_group(context, order, key_text)
    return True


def get_successful_orders(user_id):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM orders WHERE user_id = ? AND status = 'delivered' ORDER BY id DESC", (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_user_spent(user_id):
    conn = get_conn()
    row = conn.execute("SELECT COALESCE(SUM(price), 0) AS total FROM orders WHERE user_id = ? AND status = 'delivered'", (user_id,)).fetchone()
    conn.close()
    return float(row["total"] or 0)


def get_user_keys_count(user_id):
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS total FROM orders WHERE user_id = ? AND status = 'delivered'", (user_id,)).fetchone()
    conn.close()
    return int(row["total"] or 0)


def get_user_orders(user_id):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 20", (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_order(order_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_pending_orders():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM orders WHERE status = 'pending' ORDER BY id DESC LIMIT 30").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_order_status(order_id, status):
    conn = get_conn()
    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()


# ---------------- Payment (ZapUPI auto-verify, with manual-QR fallback) ----------------

ZAPUPI_CREATE_ORDER_URL = "https://pay.zapupi.com/api/create-order"
ZAPUPI_ORDER_STATUS_URL = "https://pay.zapupi.com/api/order-status"
ZAPUPI_POLL_INTERVAL_SECONDS = 12
ZAPUPI_POLL_MAX_ATTEMPTS = 25  # ~5 minutes at 12s/attempt


def create_payment_order(user_id: int, amount: float):
    # Legacy placeholder — kept only so anything still calling it directly doesn't
    # crash. The real flow is create_zapupi_order() + the manual-QR fallback below.
    return {"success": False, "error": "ZapUPI abhi set nahi hai. Admin Panel → 💳 Change ZapUPI Key se daalein."}


def _create_zapupi_order_sync(topup_id: int, amount: float, customer_mobile: str = ""):
    """Creates a UPI collect order with ZapUPI. Returns {"success": True, "payment_url":
    ..., "zap_order_id": ...} or {"success": False, "error": "..."}."""
    api_key = get_setting("zapupi_api_key", "")
    if not api_key:
        return {"success": False, "error": "ZapUPI key set nahi hai."}
    zap_order_id = f"AMAN{topup_id}{int(time.time())}"
    try:
        response = get_http_session().post(
            ZAPUPI_CREATE_ORDER_URL,
            json={
                "zap_key": api_key,
                "order_id": zap_order_id,
                "amount": f"{amount:.2f}",
                "customer_mobile": customer_mobile or "9999999999",
                "remark": f"{STORE_NAME} Wallet Topup #{topup_id}",
            },
            timeout=15,
        )
        data = response.json()
    except Exception as e:
        return {"success": False, "error": f"ZapUPI order create fail ho gaya: {e}"}
    if data.get("status") == "success" and data.get("payment_url"):
        return {"success": True, "payment_url": data["payment_url"], "zap_order_id": zap_order_id, "txn_id": data.get("txn_id")}
    logger.warning(f"ZapUPI create-order unexpected response: {data}")
    return {"success": False, "error": data.get("message") or "ZapUPI order create fail ho gaya."}


async def create_zapupi_order(topup_id: int, amount: float, customer_mobile: str = ""):
    """Async wrapper — runs the blocking HTTP call in a background thread so it
    never freezes the bot's event loop (and every other user's requests) while
    waiting on ZapUPI's servers."""
    return await asyncio.to_thread(_create_zapupi_order_sync, topup_id, amount, customer_mobile)


def _check_zapupi_order_status_sync(zap_order_id: str):
    """Polls ZapUPI for a UPI collect order's status. Returns {"payment_status":
    "Success"|"Pending"|"Failed"} (plus amount/txn_id/utr where available), or
    {"error": "..."} if the check itself failed (network/key issue)."""
    api_key = get_setting("zapupi_api_key", "")
    if not api_key:
        return {"error": "ZapUPI key set nahi hai."}
    try:
        response = get_http_session().post(
            ZAPUPI_ORDER_STATUS_URL,
            json={"zap_key": api_key, "order_id": zap_order_id},
            timeout=15,
        )
        data = response.json()
    except Exception as e:
        return {"error": f"ZapUPI status check fail: {e}"}
    if data.get("status") == "error":
        return {"error": data.get("message", "ZapUPI se unknown error.")}
    # Response shape isn't 100% confirmed from public docs (seen only via their JS
    # widget, which reads `data.data.*`) — check both a nested "data" object and
    # top-level fields so this keeps working either way.
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    raw_status = str(inner.get("status") or inner.get("payment") or "Pending").strip().capitalize()
    if raw_status not in ("Success", "Pending", "Failed"):
        raw_status = "Pending"
    return {
        "payment_status": raw_status,
        "amount": inner.get("amount"),
        "txn_id": inner.get("txn_id"),
        "utr": inner.get("utr"),
    }


async def check_zapupi_order_status(zap_order_id: str):
    """Async wrapper — runs the blocking HTTP call in a background thread."""
    return await asyncio.to_thread(_check_zapupi_order_status_sync, zap_order_id)


# ---------------- Payment (FamPay Gateway) ----------------
# API key is stored in the existing Admin Panel setting `fam_api_key`; never hard-code it.
FAMPAY_GATEWAY_BASE = "https://fampaygateway.site/api"
FAMPAY_CREATE_ORDER_URL = f"{FAMPAY_GATEWAY_BASE}/create_order.php"
FAMPAY_VERIFY_URL = f"{FAMPAY_GATEWAY_BASE}/verify.php"
FAMPAY_CHECKOUT_BASE = "https://fampaygateway.site/checkout.php"
FAM_POLL_INTERVAL_SECONDS = 10
FAM_POLL_MAX_ATTEMPTS = 30


def _create_fam_order_sync(internal_id, amount: float):
    """Create an INR payment order through FamPay Gateway."""
    api_key = get_setting("fam_api_key", "").strip()
    if not api_key:
        return {"success": False, "error": "FamPay Gateway API Key set nahi hai. Admin Panel mein Change FAM API Key karein."}
    try:
        response = get_http_session().get(
            FAMPAY_CREATE_ORDER_URL,
            params={"amount": f"{float(amount):.2f}", "api_key": api_key},
            timeout=25,
        )
        data = response.json()
    except Exception as e:
        return {"success": False, "error": f"Payment order create nahi hua: {e}"}
    if response.status_code >= 400 or not isinstance(data, dict):
        logger.warning("FamPay create-order HTTP %s: %s", response.status_code, str(data)[:500])
        return {"success": False, "error": data.get("message", "FamPay Gateway se valid response nahi mila.") if isinstance(data, dict) else "Gateway response invalid hai."}

    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    if str(data.get("status", "")).lower() != "success":
        return {"success": False, "error": data.get("message") or nested.get("message") or "FamPay Gateway order create nahi kar saka."}
    order_id = data.get("order_id") or data.get("id") or nested.get("order_id") or nested.get("id")
    qr_url = data.get("qr_url") or data.get("qr_code_url") or nested.get("qr_url") or nested.get("qr_code_url")
    if not order_id:
        logger.warning("FamPay create-order response missing order id: %s", str(data)[:500])
        return {"success": False, "error": "FamPay Gateway response mein order_id nahi mila. API key/API status check karein."}
    checkout_url = f"{FAMPAY_CHECKOUT_BASE}?order_id={quote(str(order_id))}"
    return {"success": True, "order_id": str(order_id), "qr_url": qr_url, "payment_link": checkout_url,
            "upi_id": nested.get("upi_id"), "expires_at": nested.get("expires_at")}


async def create_fam_order(internal_id, amount: float):
    return await asyncio.to_thread(_create_fam_order_sync, internal_id, amount)


def _check_fam_order_status_sync(fam_order_id: str, utr: str = ""):
    """Check FamPay payment status; this API verifies by order_id and API key (no UTR required)."""
    api_key = get_setting("fam_api_key", "").strip()
    if not api_key:
        return {"error": "FamPay Gateway API Key set nahi hai."}
    try:
        response = get_http_session().get(
            FAMPAY_VERIFY_URL,
            params={"order_id": str(fam_order_id), "api_key": api_key},
            timeout=25,
        )
        data = response.json()
    except Exception as e:
        return {"error": f"Payment verification fail: {e}"}
    if response.status_code >= 400 or not isinstance(data, dict):
        logger.warning("FamPay verify HTTP %s for order %s: %s", response.status_code, fam_order_id, str(data)[:500])
        return {"error": data.get("message", "FamPay verification request fail hui.") if isinstance(data, dict) else "Gateway response invalid hai."}
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    raw_status = str(data.get("status", inner.get("status", "pending"))).strip().lower()
    if raw_status == "success":
        return {"payment_status": "Success", "amount": inner.get("amount"),
                "txn_id": inner.get("transaction_id") or inner.get("payment_time"), "utr": inner.get("utr") or fam_order_id}
    if raw_status in ("failed", "failure", "expired", "cancelled", "canceled"):
        return {"payment_status": "Failed", "message": data.get("message") or raw_status}
    return {"payment_status": "Pending", "message": data.get("message") or "Payment abhi pending hai."}


async def check_fam_order_status(fam_order_id: str, utr: str = ""):
    return await asyncio.to_thread(_check_fam_order_status_sync, fam_order_id, utr)


# ---------------- Reseller Key API (xyzcheats.com reseller panel) ----------------

RESELLER_API_BASE = "https://bantibhaiya.to/api/reseller_v1.php"
RESELLER_API_KEY = "PUT_YOUR_RESELLER_API_KEY_HERE"                # existing Banti fallback; Admin Panel value is preferred
ADITYA_API_BASE = "https://adityareseller.shop/aditya/api/reseller"


def extract_duration(product_name: str) -> str:
    """Pulls the full duration text out of a plan label like 'Mod Menu (7 Days)'
    -> '7 Days' — this is what the reseller panel's API expects (a duration
    string like '1 Day' / '3 Hours' / '7 Days', not just a bare number).
    Falls back to '1 Day' if nothing parenthesized is found."""
    import re
    match = re.search(r"\(([^)]+)\)", product_name)
    return match.group(1).strip() if match else "1 Day"


def _buy_key_from_reseller_sync(api_product_code: str, order_id: int, duration: str = "1 Day", android_id: str = None):
    """
    Calls the reseller panel to automatically purchase/fetch a key for the given
    product code. Returns {"success": True, "key": "..."} or {"success": False, "error": "..."}.

    Confirmed from the panel's own docs/sample code: POST to reseller_v1.php with
    api_key, action="buy", product_id, duration (a string like "1 Day"/"7 Days"),
    and android_id for device-bound products (e.g. BALA MOD XYZ V1) — plus an
    'x-master-key' request header.

    TODO: response field name for the key is still a guess ("key"/"license"/"data")
    — confirm once a real successful response is seen, and adjust below if needed.
    """
    api_key = get_setting("reseller_api_key", "") or RESELLER_API_KEY
    if not api_key or api_key == "PUT_YOUR_RESELLER_API_KEY_HERE":
        return {"success": False, "error": "Reseller API key set nahi hai. Admin Panel → 🔑 Change Reseller API Key se daalein."}
    master_key = get_setting("reseller_master_key", "")
    try:
        payload = {
            "api_key": api_key,
            "action": "buy",
            "product_id": api_product_code,
            "duration": duration,
        }
        if android_id:
            payload["android_id"] = android_id
        headers = {}
        if master_key:
            headers["x-master-key"] = master_key

        response = get_http_session().post(RESELLER_API_BASE, data=payload, headers=headers, timeout=15)
        try:
            data = response.json()
        except ValueError:
            if response.status_code != 200:
                return {"success": False, "error": f"HTTP {response.status_code}: {response.text[:300]}"}
            # Some panels return the key as plain text instead of JSON.
            text = response.text.strip()
            if text:
                return {"success": True, "key": text}
            return {"success": False, "error": "Reseller se khaali response mila."}

        logger.info(f"Reseller API response for order #{order_id}: {data}")  # debug — never includes api_key/master_key, just the panel's own reply

        # Accept the common response shapes without allowing an unexpected JSON
        # list/string/null response to crash the delivery handler.
        if isinstance(data, dict):
            key = data.get("key") or data.get("license") or data.get("data") or data.get("result") or data.get("output")
            if isinstance(key, dict):
                key = key.get("key") or key.get("license") or key.get("value") or key.get("output")
            if isinstance(key, (list, tuple)):
                key = "\n".join(str(item) for item in key if str(item).strip())
            if key and str(key).strip():
                return {"success": True, "key": str(key).strip()}
            raw_snippet = str(data)[:400]
            base_msg = data.get("msg") or data.get("message") or data.get("error") or data.get("status") or "Reseller ne key nahi bheji."
            return {"success": False, "error": f"{base_msg}\n\nRaw response: {raw_snippet}"}

        raw_snippet = str(data)[:400]
        if raw_snippet.strip():
            return {"success": False, "error": f"Reseller ka unexpected response mila.\n\nRaw response: {raw_snippet}"}
        return {"success": False, "error": "Reseller se khaali response mila."}
    except requests.RequestException as e:
        logger.warning(f"Reseller API call failed: {e}")
        return {"success": False, "error": f"Reseller API se connect nahi ho paya: {e}"}


def _aditya_request_sync(path, method="GET", payload=None):
    api_key = get_setting("aditya_api_key", "").strip()
    if not api_key:
        return {"success": False, "error": "Aditya API key set nahi hai. Admin Panel → ADITYA API KEY → Change API Key se daalein."}
    url = ADITYA_API_BASE.rstrip("/") + "/" + path.lstrip("/")
    headers = {"X-Api-Key": api_key, "Accept": "application/json"}
    try:
        if method.upper() == "POST":
            response = get_http_session().post(url, json=payload or {}, headers=headers, timeout=20)
        else:
            response = get_http_session().get(url, headers=headers, timeout=20)
        try:
            data = response.json()
        except ValueError:
            return {"success": False, "error": f"Aditya API HTTP {response.status_code}: {response.text[:400]}"}
        if response.status_code != 200:
            msg = data.get("message") or data.get("error") or data.get("status") if isinstance(data, dict) else str(data)
            return {"success": False, "error": f"Aditya API HTTP {response.status_code}: {msg}"}
        return data if isinstance(data, dict) else {"success": False, "error": "Aditya API ka response format unexpected hai."}
    except requests.RequestException as e:
        logger.warning("Aditya API call failed: %s", e)
        return {"success": False, "error": f"Aditya API se connect nahi ho paya: {e}"}


def _aditya_products_sync():
    data = _aditya_request_sync("products.php")
    if data.get("success") is False and data.get("error"):
        return data
    products = data.get("products") if isinstance(data, dict) else None
    if products is None and isinstance(data, dict):
        products = data.get("data")
    if products is None:
        products = []
    if isinstance(products, dict):
        products = list(products.values())
    return {"success": True, "products": products, "balance": data.get("balance") if isinstance(data, dict) else None}


def _aditya_generate_sync(product, plan, order_id):
    product_id = product.get("api_product_code")
    slug = product.get("api_product_slug")
    duration_id = plan.get("api_duration_id")
    duration_value = plan.get("api_duration_value")
    unit = (plan.get("api_duration_unit") or "days").lower()
    payload = {"quantity": 1}
    if product_id and duration_id:
        payload.update({"product_id": str(product_id), "duration_id": str(duration_id)})
    elif slug and duration_value is not None:
        payload.update({"product_slug": slug, "duration_days": float(duration_value) if unit == "days" else duration_value})
    else:
        return {"success": False, "error": "Aditya product/duration mapping incomplete hai."}
    data = _aditya_request_sync("generate.php", method="POST", payload=payload)
    if data.get("success") is not True:
        return {"success": False, "error": data.get("message") or data.get("error") or "Aditya ne key generate nahi ki."}
    keys = data.get("keys") or []
    if isinstance(keys, str):
        keys = [keys]
    if not keys:
        return {"success": False, "error": "Aditya API success boli, lekin key nahi mili."}
    return {"success": True, "key": str(keys[0]), "response": data}


async def buy_key_from_aditya(product, plan, order_id: int):
    return await asyncio.to_thread(_aditya_generate_sync, product, plan, order_id)


async def buy_key_for_product(product, order, order_id: int):
    source = str(product.get("reseller_source") or "banti").lower()
    if source == "aditya":
        plan = get_plan(order.get("plan_id")) if order.get("plan_id") else None
        if not plan:
            return {"success": False, "error": "Order ka Aditya duration plan nahi mila."}
        return await buy_key_from_aditya(product, plan, order_id)
    duration = get_order_api_duration(order)
    return await buy_key_from_reseller(product["api_product_code"], order_id, duration=duration, android_id=order.get("android_id"))


async def buy_key_from_reseller(api_product_code: str, order_id: int, duration: str = "1 Day", android_id: str = None):
    """Async wrapper — runs the blocking Banti HTTP call in a background thread."""
    return await asyncio.to_thread(_buy_key_from_reseller_sync, api_product_code, order_id, duration, android_id)


def _generate_upi_qr_sync(amount: float, user_id: int, note: str = None):
    """Builds a UPI deep-link for the given amount using the admin-configured UPI ID
    and returns a QR code image (BytesIO, ready to send as a photo). Returns None
    if no UPI ID has been set yet by an admin."""
    upi_id = get_setting("upi_id", "")
    if not upi_id:
        return None
    note = note or f"{STORE_NAME} topup {user_id}"
    upi_link = (
        f"upi://pay?pa={quote(upi_id)}&pn={quote(STORE_NAME)}"
        f"&am={amount:.2f}&cu=INR&tn={quote(note)}"
    )
    img = qrcode.make(upi_link)
    buf = BytesIO()
    buf.name = "upi_qr.png"
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


async def generate_upi_qr(amount: float, user_id: int, note: str = None):
    """Async wrapper — QR image generation is CPU-bound; runs in a background
    thread so it never blocks the event loop under load."""
    return await asyncio.to_thread(_generate_upi_qr_sync, amount, user_id, note)


def reconciliation_amount(base_amount, record_id):
    """Adds a small, order-ID-derived paise amount (e.g. ₹50 → ₹50.37) so every
    manual UPI QR has a unique total — this makes it far easier for the admin to
    spot the exact matching transaction in their bank/UPI app history when
    confirming a payment by hand. Deterministic per record_id, so re-showing the
    same QR (e.g. after a 'Check Status' tap) always shows the same amount."""
    jitter = (record_id % 99 + 1) / 100
    return round(base_amount + jitter, 2)


def manual_upi_caption(pay_amount, order_ref, extra_note=""):
    """Builds the 'UPI PAYMENT' info-card caption shown above the QR for the manual
    (non-ZapUPI) payment flow — amount, order ref, UPI ID, and step-by-step instructions."""
    upi_id = get_setting("upi_id", "")
    lines = [
        "🔐 *UPI PAYMENT*",
        "────────────",
        "",
        f"— 💰 Amount: ₹{pay_amount:.2f} INR",
        f"— 🔒 Order: `{escape_md(order_ref)}`",
    ]
    if upi_id:
        lines.append(f"— 🆔 UPI ID: `{escape_md(upi_id)}`")
    lines.append("— ⏰ Valid for: 5:00 minutes")
    if extra_note:
        lines += ["", extra_note.rstrip()]
    lines += [
        "",
        "ℹ️ *How to pay*",
        "— 1️⃣ Scan the QR with any UPI app (GPay, PhonePe, Paytm)",
    ]
    if upi_id:
        lines.append("— 2️⃣ Ya UPI ID tap karke copy kar lein")
    lines += [
        f"— 3️⃣ *Exactly* ₹{pay_amount:.2f} INR pay karein",
        "— 4️⃣ Niche '✅ Verify Payment' dabayein — admin confirm karte hi turant deliver ho jayega",
        "",
        "⚠️ _EXACT amount pay karein, warna match nahi hoga._",
        "_Ye QR single-use hai._",
    ]
    return "\n".join(lines)


# ---------------- Menu builders ----------------

def main_menu_keyboard(user_id=None):
    def row_of(*items):
        """items: list of (text, callback_data, style). Filters out hidden ones,
        drops the row entirely if nothing's left in it."""
        kept = [InlineKeyboardButton(text, callback_data=cb, style=style)
                for text, cb, style in items if not is_button_hidden(cb)]
        return [kept] if kept else []

    rows = []
    # Main menu layout requested by the user.
    rows += row_of(("🛒 Shop Now", "menu:shop", "danger"))
    rows += row_of(("📣 Check Update", "menu:updates", "success"), ("💰 Add Balance", "menu:balance", "primary"))
    rows += row_of(("👑 My Profile + Key History", "menu:profilekeys", "success"))
    rows += row_of(("🎡 Lucky Spin", "menu:wheel", "success"), ("👑 Reseller", "menu:premium", "primary"))
    rows += row_of(("📺 How to Use", "menu:howto", "primary"), ("💬 Support", "menu:support", "danger"))
    rows += row_of(("🎁 Refer & Earn", "menu:referral", "success"))

    for cb in list_custom_buttons():
        if cb["action_type"] == "url":
            rows.append([InlineKeyboardButton(cb["label"], url=cb["action_value"])])
        else:
            rows.append([InlineKeyboardButton(cb["label"], callback_data=f"custombtn:{cb['id']}")])

    if user_id is not None and is_admin(user_id):
        rows.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin:main", style="primary")])
    return IKM(rows)


def selling_proofs_settings_keyboard():
    enabled = get_setting("selling_proofs_enabled", "1") == "1"
    return IKM([
        [InlineKeyboardButton(f"🧾 Selling Proofs: {'ON' if enabled else 'OFF'}", callback_data="admin:toggle_sellingproofs")],
        [InlineKeyboardButton("🔗 Set Selling Proofs Channel Link", callback_data="admin:set_sellingproofs_link")],
        [InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")],
    ])


def admin_menu_keyboard():
    return IKM([
        [InlineKeyboardButton("➕ Add Product", callback_data="admin:addproduct")],
        [InlineKeyboardButton("📅 Add Plan / Duration", callback_data="admin:addplan")],
        [InlineKeyboardButton("🗑️ Remove Product", callback_data="admin:removeproduct")],
        [InlineKeyboardButton("✏️ Edit Price", callback_data="admin:editprice")],
        [InlineKeyboardButton("📝 Edit Description", callback_data="admin:editdesc")],
        [InlineKeyboardButton("🔗 Get Product Link", callback_data="admin:productlink")],
        [InlineKeyboardButton("🔌 Set Delivery Mode", callback_data="admin:setdelivery")],
        [InlineKeyboardButton("📦 Products", callback_data="admin:manageproducts")],
        [InlineKeyboardButton("🛠️ Product Maintenance", callback_data="admin:maintenance")],
        [InlineKeyboardButton("🔢 Set Product / Category Order", callback_data="admin:ordering")],
        [InlineKeyboardButton("🗂️ Set Category Icon", callback_data="admin:setcategoryicon")],
        [InlineKeyboardButton("⏱️ Set Plan API Duration", callback_data="admin:setapiduration")],
        [InlineKeyboardButton("👑 Fix Price for Resellers", callback_data="admin:setresellerprice")],
        [InlineKeyboardButton("🔢 Set Product PID", callback_data="admin:setpid")],
        [InlineKeyboardButton("🎨 Set Product Icon", callback_data="admin:seticon")],
        [InlineKeyboardButton("🖼️ Set Product Photo", callback_data="admin:setphoto")],
        [InlineKeyboardButton("🌐 BANTI API KEY", callback_data="admin:banti_keys")],
        [InlineKeyboardButton("🌐 ADITYA API KEY", callback_data="admin:aditya_keys")],
        [InlineKeyboardButton("💳 Change ZapUPI Key", callback_data="admin:setzapupikey")],
        [InlineKeyboardButton(f"📱 Verify Required: {'✅ ON' if is_verification_required() else '❌ OFF'}", callback_data="admin:toggleverify")],
        [InlineKeyboardButton("💳 Change FAM API Key", callback_data="admin:setfamkey")],
        [InlineKeyboardButton("📦 Pending Orders", callback_data="admin:pendingorders")],
        [InlineKeyboardButton("📢 Broadcast Message", callback_data="admin:broadcast")],
        [InlineKeyboardButton("📩 SMS Auto-Verify Setup Guide", callback_data="admin:smsguide")],
        [InlineKeyboardButton("📢 Change Update Channel", callback_data="admin:setchannel")],
        [InlineKeyboardButton("📢 Set Purchase Proof Channel", callback_data="admin:setproofchannel")],
        [InlineKeyboardButton("🧾 Selling Proofs Settings", callback_data="admin:sellingproofs")],
        [InlineKeyboardButton("💳 Change UPI ID", callback_data="admin:setupi")],
        [InlineKeyboardButton("🎬 Set How To Video Link", callback_data="admin:sethowtovideo")],
        [InlineKeyboardButton("🤖 Set Bot Username", callback_data="admin:setbotusername")],
        [InlineKeyboardButton("📞 Set Contact Admin Username", callback_data="admin:setadmincontact")],
        [InlineKeyboardButton("👑 Premium Settings", callback_data="admin:setpremium")],
        [InlineKeyboardButton("🎟️ Coupon Codes", callback_data="admin:coupons")],
        [InlineKeyboardButton("👑 Add Admin", callback_data="admin:addadmin"),
         InlineKeyboardButton("📋 List Admins", callback_data="admin:listadmins")],
        [InlineKeyboardButton("🗑️ Remove Admin", callback_data="admin:removeadmin")],
        [InlineKeyboardButton("👑 Add Reseller (User)", callback_data="admin:addreseller")],
        [InlineKeyboardButton("🚫 Remove Reseller (User)", callback_data="admin:removereseller")],
        [InlineKeyboardButton("💰 Add Balance User", callback_data="admin:addbalanceuser")],
        [InlineKeyboardButton("🎨 Button Colors", callback_data="admin:buttoncolors")],
        [InlineKeyboardButton("✏️ Edit Button Name", callback_data="admin:buttonnames")],
        [InlineKeyboardButton("🔣 Back to Menu Icon", callback_data="admin:backmenuicon")],
        [InlineKeyboardButton("🧩 Manage Buttons", callback_data="admin:managebuttons")],
        [InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")],
    ])


def button_color_groups_keyboard():
    rows = [[InlineKeyboardButton(g, callback_data=f"btncolor_group:{g}")] for g in BUTTON_REGISTRY_GROUPS]
    rows.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
    return IKM(rows)


def button_color_list_keyboard(group):
    swatch = {"primary": "🔵", "success": "🟢", "danger": "🔴", None: "⚪"}
    rows = []
    for key, label, g, _default in BUTTON_REGISTRY:
        if g != group:
            continue
        current = swatch[get_button_style(key)]
        rows.append([InlineKeyboardButton(f"{current} {label}", callback_data=f"btncolor_pick:{key}")])
    rows.append([InlineKeyboardButton("⬅ Back to Button Colors", callback_data="admin:buttoncolors")])
    return IKM(rows)


def button_color_picker_keyboard(key):
    rows = [[InlineKeyboardButton(label, callback_data=f"btncolor_set:{key}:{code}", style=style)]
            for code, label, style in BUTTON_COLOR_CHOICES]
    key_group = next((g for k, _l, g, _d in BUTTON_REGISTRY if k == key), None)
    rows.append([InlineKeyboardButton("⬅ Back to List", callback_data=f"btncolor_group:{key_group}")])
    return IKM(rows)


def button_name_groups_keyboard():
    rows = [[InlineKeyboardButton(g, callback_data=f"btnname_group:{g}")] for g in BUTTON_REGISTRY_GROUPS]
    rows.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
    return IKM(rows)


def button_name_list_keyboard(group):
    rows = []
    for key, label, g, _default in BUTTON_REGISTRY:
        if g != group:
            continue
        mark = "✏️ " if get_button_label_override(key) else ""
        rows.append([InlineKeyboardButton(f"{mark}{label}", callback_data=f"btnname_pick:{key}")])
    rows.append([InlineKeyboardButton("⬅ Back to Edit Button Name", callback_data="admin:buttonnames")])
    return IKM(rows)


def button_name_prompt_keyboard(key, group):
    return IKM([
        [InlineKeyboardButton("🔄 Reset to Default", callback_data=f"btnname_reset:{key}")],
        [InlineKeyboardButton("⬅ Back to List", callback_data=f"btnname_group:{group}")],
    ])


def manage_buttons_keyboard():
    return IKM([
        [InlineKeyboardButton("👁️ Show/Hide Buttons", callback_data="admin:hidebuttons")],
        [InlineKeyboardButton("➕ Add Custom Button", callback_data="admin:addcustombtn")],
        [InlineKeyboardButton("🗑️ Remove Custom Button", callback_data="admin:removecustombtn")],
        [InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")],
    ])


def hide_buttons_groups_keyboard():
    rows = [[InlineKeyboardButton(g, callback_data=f"hidebtn_group:{g}")] for g in BUTTON_REGISTRY_GROUPS]
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="admin:managebuttons")])
    return IKM(rows)


_HIDE_PROTECTED_KEYS = {"admin:main", "admin:managebuttons", "admin:hidebuttons"}


def hide_buttons_list_keyboard(group):
    rows = []
    for key, label, g, _default in BUTTON_REGISTRY:
        if g != group or key in _HIDE_PROTECTED_KEYS:
            continue
        mark = "🚫" if is_button_hidden(key) else "👁️"
        rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"togglehide:{key}")])
    rows.append([InlineKeyboardButton("⬅ Back to Groups", callback_data="admin:hidebuttons")])
    return IKM(rows)


def custom_button_type_keyboard():
    return IKM([
        [InlineKeyboardButton("🔗 URL Link", callback_data="addcustombtn_type:url")],
        [InlineKeyboardButton("💬 Text Message", callback_data="addcustombtn_type:text")],
        [InlineKeyboardButton("⬅ Back", callback_data="admin:managebuttons")],
    ])


def remove_custom_buttons_keyboard():
    buttons = list_custom_buttons()
    rows = [[InlineKeyboardButton(f"🗑️ {b['label']}", callback_data=f"delcustombtn:{b['id']}")] for b in buttons]
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="admin:managebuttons")])
    return IKM(rows)


def is_text_block_overridden(key):
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM text_blocks WHERE block_key = ?", (key,)).fetchone()
    conn.close()
    return row is not None


def manage_descriptions_keyboard():
    """Single admin menu for every user-facing description/text, except Add Balance."""
    rows = []
    groups = [
        ("📦 Product Description", "products"),
        ("🗂️ Category Selection Description (use {category} {first_name})", "shop_category_header"),
        ("🛍️ Product Price List Description (use {product_name} {first_name})", "shop_product_header"),
        ("🧾 Confirm Your Order Description", "order_confirmation_text"),
        ("💰 Add Balance Description (use {amount} {balance})", "add_balance_text"),
        ("🏠 /start Welcome Text", "welcome"),
        ("🛒 Shop Description", "shop_panel_select"),
        ("📝 My Orders Description", "orders_text"),
        ("📢 Purchase Proof Description (use {first_name} {user_id} {product_name} {duration} {bot_username})", "purchase_proof_text"),
        ("🧾 Selling Proofs Description", "selling_proofs_text"),
        ("👤 Profile Description", "profile_text"),
        ("👑 Reseller Description", "reseller_text"),
        ("🎡 Lucky Spin Description", "wheel_text"),
        ("📣 Updates Description", "updates_text"),
        ("📺 How To Description", "howto_text_block"),
        ("🎉 Share Description", "share_text"),
        ("🎁 Refer & Earn Description", "referral_text"),
        ("💬 Support Description", "support_text"),
        ("📱 Verify Account Description", "verify_text"),
        ("⏳ Payment Pending Description", "qr_verify_pending"),
        ("❌ Order / QR Cancelled Description", "order_cancelled_text"),
        ("⏰ Order / QR Expired Description", "order_expired_text"),
        ("🔑 Delivery / Success Description", "delivery_receipt"),
    ]
    for label, key in groups:
        if key == "products":
            cb = "descmenu:products"
        else:
            cb = f"descblock:{key}"
        mark = "✏️ " if key != "products" and is_text_block_overridden(key) else ""
        rows.append([InlineKeyboardButton(mark + label, callback_data=cb)])
    rows.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
    return IKM(rows)


def description_prompt_keyboard(key):
    return IKM([
        [InlineKeyboardButton("🔄 Reset to Default", callback_data=f"descreset:{key}")],
        [InlineKeyboardButton("⬅ Back to List", callback_data="admin:editdesc")],
    ])


def admin_back_keyboard():
    return IKM([[InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")]])


def product_order_list_keyboard():
    products = list_products(active_only=True)
    rows = []
    for p in products:
        pos = get_product_order(p["id"]) or "—"
        rows.append([InlineKeyboardButton(f"{pos}. {p['name']}", callback_data=f"admproductorder:{p['id']}")])
    rows.append([InlineKeyboardButton("🗂️ Set Category Order", callback_data="admin:categoryorder")])
    rows.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
    return IKM(rows)


def product_order_positions_keyboard(pid):
    product = get_product(pid)
    if not product:
        return admin_back_keyboard()
    items = [p for p in list_products_by_category(product.get("category") or "General")]
    rows = []
    for pos, p in enumerate(items, 1):
        mark = " ✅" if p["id"] == pid else ""
        rows.append([InlineKeyboardButton(f"{pos}. {p['name']}{mark}", callback_data=f"setprodorder:{pid}:{pos}")])
    rows.append([InlineKeyboardButton("⬅ Back to Product Order", callback_data="admin:ordering")])
    return IKM(rows)


def category_order_keyboard():
    categories = list_categories()
    rows = []
    for pos, cat in enumerate(categories, 1):
        rows.append([InlineKeyboardButton(f"{pos}. {cat}", callback_data=f"setcatorder:{quote(cat, safe='')}")])
    rows.append([InlineKeyboardButton("⬅ Back to Product / Category Order", callback_data="admin:ordering")])
    return IKM(rows)


def category_order_positions_keyboard(category):
    categories = list_categories()
    rows = []
    for pos, cat in enumerate(categories, 1):
        mark = " ✅" if cat == category else ""
        rows.append([InlineKeyboardButton(f"{pos}. {cat}{mark}", callback_data=f"move_cat:{quote(category, safe='')}:{pos}")])
    rows.append([InlineKeyboardButton("⬅ Back to Category Order", callback_data="admin:categoryorder")])
    return IKM(rows)


def product_link_list_keyboard():
    products = list_products(active_only=True)
    rows = [[InlineKeyboardButton(f"{p['name']} (ID: {p['id']})", callback_data=f"productlink:{p['id']}")] for p in products]
    rows.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
    return IKM(rows)


def product_link_result_keyboard(pid, link):
    encoded = quote(link, safe="")
    return IKM([
        [InlineKeyboardButton("🔗 Open Product", url=link)],
        [InlineKeyboardButton("📤 Share Product Link", url=f"https://t.me/share/url?url={encoded}")],
        [InlineKeyboardButton("⬅ Back to Product Links", callback_data="admin:productlink")],
    ])


def maintenance_updates_url(channel_link):
    """Build the Check Updates URL while keeping the destination as the product's channel.

    A URL fragment is ignored by Telegram's channel navigation but gives the
    button-color/name system a stable marker so CHECK UPDATES can be customized
    independently from other buttons that may open the same channel.
    """
    channel = str(channel_link or "").strip()
    if not channel:
        return ""
    if channel.startswith("@"):
        channel = f"https://t.me/{channel[1:]}"
    elif channel.startswith("t.me/"):
        channel = f"https://{channel}"
    if "#" in channel:
        return channel.split("#", 1)[0] + "#maintenance_updates"
    return channel + "#maintenance_updates"


def product_quick_config_keyboard(pid):
    """All-in-one config panel shown right after a product is added, or when
    opened via Admin Panel → 📦 Products — every per-product setting reachable
    in one tap each, no need to keep re-navigating and picking the product again."""
    return IKM([
        [InlineKeyboardButton("📅 Add Plan/Duration", callback_data=f"addplanprod:{pid}")],
        [InlineKeyboardButton("🔌 Set Delivery Mode (API/Manual)", callback_data=f"admdel:{pid}")],
        [InlineKeyboardButton("🔢 Set Product PID", callback_data=f"admsetpid:{pid}")],
        [InlineKeyboardButton("⏱️ Set Plan API Duration", callback_data=f"admapidur_prod:{pid}")],
        [InlineKeyboardButton("🎨 Set Product Icon", callback_data=f"admicon:{pid}")],
        [InlineKeyboardButton("🖼️ Set Product Photo", callback_data=f"admphoto:{pid}")],
        [InlineKeyboardButton("📢 Set Product Channel", callback_data=f"admchannel:{pid}")],
        [InlineKeyboardButton("🛠️ Product Maintenance", callback_data=f"admmaintenance:{pid}")],
        [InlineKeyboardButton("🔢 Set Product Order", callback_data=f"admproductorder:{pid}")],
        [InlineKeyboardButton("⬅ Back to Products", callback_data="admin:manageproducts")],
    ])


def api_duration_plan_list_keyboard(pid):
    plans = get_plans(pid)
    if not plans:
        return admin_back_keyboard()
    rows = [[InlineKeyboardButton(f"{pl['label']}" + (f" (API: {pl['api_duration']})" if pl.get("api_duration") else " (API duration not set)"),
                                   callback_data=f"admapidur_plan:{pl['id']}")] for pl in plans]
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="admin:setapiduration")])
    return IKM(rows)


def reseller_price_plan_list_keyboard(pid):
    plans = get_plans(pid)
    if not plans:
        return admin_back_keyboard()
    rows = [[InlineKeyboardButton(f"{pl['label']} — ₹{pl['price']:.0f}" + (f" (reseller: ₹{pl['reseller_price']:.0f})" if pl.get("reseller_price") is not None else ""),
                                   callback_data=f"admresprice_plan:{pl['id']}")] for pl in plans]
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="admin:setresellerprice")])
    return IKM(rows)


def mykeys_product_list_keyboard(user_id):
    """My Keys shows only products the user has actually purchased.
    A single Not Purchased option opens the fixed admin-editable description;
    it never lists or opens individual unpurchased product descriptions/plans.
    """
    orders = get_successful_orders(user_id)
    counts = {}
    names = {}
    for o in orders:
        pid = o.get("product_id")
        if pid is None:
            continue
        counts[pid] = counts.get(pid, 0) + 1
        names[pid] = o.get("product_name") or "Product"

    products = list_products(active_only=True)
    rows = []
    # Only products actually purchased by this user are shown here.
    for p in products:
        pid = p.get("id")
        if pid in counts:
            rows.append([InlineKeyboardButton(
                f"🔑 {p.get('name') or names.get(pid, 'Product')} ({counts[pid]})",
                callback_data=f"mykeys:product:{pid}"
            )])

    # No separate "Not Purchased" button. The fixed editable description is shown
    # directly on the My Keys screen for everything the user has not purchased.
    rows.append([InlineKeyboardButton("⬅ Back to Profile", callback_data="menu:profile")])
    return IKM(rows)


def product_purchase_keyboard(user_id, pid):
    product = get_product(pid)
    if not product or not product.get("active"):
        return back_keyboard()
    plans = get_plans(pid)
    rows = []
    user_is_premium = is_premium(user_id)
    for pl in plans:
        final_price, _note = compute_final_price(user_id, pl)
        label = f"{pl['label']} — ₹{final_price:.0f}" if user_is_premium and final_price != pl["price"] else f"{pl['label']} — ₹{pl['price']:.0f}"
        rows.append([InlineKeyboardButton(label, callback_data=f"shop_plan:{pl['id']}")])
    rows.append([InlineKeyboardButton("⬅ Back to My Keys", callback_data="menu:mykeys")])
    return IKM(rows)


def purchased_product_text(user_id, pid):
    """Build the per-product key history shown after clicking a purchased product."""
    orders = [o for o in get_successful_orders(user_id) if o.get("product_id") == pid]
    product = get_product(pid)
    pname = (product.get("name") if product else None) or (orders[0].get("product_name") if orders else "Product")
    lines = [f"🔑 *{escape_md(pname)}*", "", f"You've bought {len(orders)} keys from this product:", "━━━━━━━━━━━━"]
    for o in orders:
        key_text = o.get("delivered_key") or "Key record available nahi hai"
        date_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(o.get("delivery_time") or o.get("created_at") or time.time()))
        full_name = o.get("product_name") or ""
        plan_label = ""
        if "(" in full_name and full_name.endswith(")"):
            plan_label = full_name.rsplit("(", 1)[1].rstrip(")").strip()
        lines.append(f"🕘 {date_str}" + (f" — {escape_md(plan_label)}" if plan_label else ""))
        lines.append(f"🔑 `{escape_md(key_text)}`")
        lines.append("━━━━━━━━━━━━")

    channel = product.get("channel_link") if product else ""
    rows = []
    if channel:
        url = channel if channel.startswith("http") else f"https://t.me/{channel.lstrip('@')}"
        rows.append([InlineKeyboardButton("📢 Product Channel", url=url)])
    rows.append([InlineKeyboardButton("⬅ Back to My Keys", callback_data="menu:mykeys")])
    return "\n".join(lines), IKM(rows)


def profile_keyboard():
    """Profile actions shown below the user's profile photo/text."""
    return IKM([
        [
            InlineKeyboardButton("🔑 My Keys", callback_data="menu:mykeys"),
            InlineKeyboardButton("🛒 Shop Now", callback_data="menu:shop"),
        ],
        [InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")],
    ])


def mykeys_keyboard(user_id):
    return mykeys_product_list_keyboard(user_id)


def back_keyboard():
    return IKM([[InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")]])


def verify_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Verify Account", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )


def verify_intro_text(first_name):
    """Verification screen comes from the same editable description system."""
    return render_text_block("verify_text", first_name=first_name)


def get_log_group(kind: str) -> int:
    """kind is one of: newuser, keys, referral.
    Checks the admin-configurable setting first (set via /groupid buttons),
    falls back to the CONFIG constant so existing setups keep working."""
    fallback = {"newuser": NEW_USER_LOG_GROUP_ID, "keys": KEY_LOG_GROUP_ID, "referral": REFERRAL_LOG_GROUP_ID}[kind]
    val = get_setting(f"log_group_{kind}", "")
    try:
        return int(val) if val else fallback
    except ValueError:
        return fallback


async def notify_group(context: ContextTypes.DEFAULT_TYPE, group_id: int, text: str, entities=None):
    """Sends a log message to an internal ops group, if configured. Never crashes the bot if it fails."""
    if not group_id:
        return
    try:
        if entities:
            await safe_send_message(context.bot, chat_id=group_id, text=text, entities=entities)
        else:
            await safe_send_message(context.bot, chat_id=group_id, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Could not send to log group {group_id}: {e}")


async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    """Sends a message (optionally with buttons) to every admin — config-listed + DB-added.
    Skips admins who haven't started the bot yet (Telegram blocks that silently)."""
    all_admin_ids = set(ADMIN_IDS) | {a["user_id"] for a in list_admins_db()}
    for admin_id in all_admin_ids:
        try:
            await safe_send_message(context.bot, chat_id=admin_id, text=text, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")


def welcome_text(first_name, balance=None):
    return render_text_block("welcome", store_name=escape_md(STORE_NAME), first_name=escape_md(first_name), balance=f"{balance:.2f}" if balance is not None else "")


# ---------------- Handlers ----------------

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS or is_admin_db(user_id)


async def send_upi_qr(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, amount: float,
                       via_query: bool = False, note: str = None, caption: str = None, reply_markup=None):
    """Generates and sends a UPI QR code for the exact amount. Falls back to the
    FAM API / plain error message if no UPI ID has been configured by an admin yet."""
    qr = await generate_upi_qr(amount, user_id, note=note)
    chat_id = update.effective_chat.id
    if qr is None:
        result = create_payment_order(user_id, amount)
        text = f"💳 Payment link: {result['payment_url']}" if result.get("success") else f"⚠️ {result.get('error')}"
        if via_query:
            await safe_edit_message_text(update.callback_query, text, reply_markup=back_keyboard())
        else:
            await safe_reply_text(update.message, text, reply_markup=back_keyboard())
        return
    if caption is None:
        caption = (
            f"💳 *Scan & Pay ₹{amount:.0f}*\n"
            f"UPI se scan karke exact amount pay karein.\n\n"
            f"Payment ke baad screenshot/UTR Support mein bhejein, balance verify hoke add ho jayega."
        )
    await safe_send_photo(context.bot, 
        chat_id=chat_id, photo=qr, caption=caption, parse_mode="Markdown",
        reply_markup=reply_markup or back_keyboard(),
    )
    if via_query:
        try:
            await update.callback_query.delete_message()
        except Exception:
            pass


async def start_wallet_topup(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, amount: float, via_query: bool):
    """Starts a wallet top-up. Priority: FamAPI (own UPI, instant auto-verify) →
    ZapUPI (if configured) → old manual UPI QR + admin-approve flow. Nothing
    breaks if the higher tiers aren't set up or their API call fails."""
    tid = create_topup(user_id, amount)
    loading_message = await send_chat_loading(context.bot, update.effective_chat.id)

    if get_setting("fam_api_key", ""):
        fam_result = await create_fam_order(tid, amount)
        if fam_result.get("success"):
            set_topup_fam_order(tid, fam_result["order_id"])
            caption = (
                f"💳 *₹{amount:.0f} ka payment*\n"
                f"Neeche QR scan karein ya Pay Now dabayein.\n\n"
                f"Payment ke baad FamPay Gateway status automatically check karega aur successful payment par wallet balance add ho jayega."
            )
            fam_rows = []
            if fam_result.get("payment_link"):
                fam_rows.append([InlineKeyboardButton("💳 Pay Now", url=fam_result["payment_link"])])
            fam_rows.extend([
                [InlineKeyboardButton("🔄 Check Status Now", callback_data=f"fam_check:topup:{tid}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"qrcancel_topup:{tid}")],
            ])
            buttons = IKM(fam_rows)
            chat_id = update.callback_query.message.chat_id if via_query else update.effective_chat.id
            if via_query:
                try:
                    await update.callback_query.delete_message()
                except Exception:
                    pass
            if fam_result.get("qr_url"):
                await safe_send_photo(context.bot, chat_id=chat_id, photo=fam_result["qr_url"], caption=caption, parse_mode="Markdown", reply_markup=buttons)
            else:
                await safe_send_message(context.bot, chat_id=chat_id, text=caption + "\n\nQR/checkout kholne ke liye Pay Now dabayein.", parse_mode="Markdown", reply_markup=buttons)
            await delete_chat_loading(context.bot, loading_message)
            if context.job_queue is not None:
                context.job_queue.run_repeating(
                    fam_poll_job,
                    interval=FAM_POLL_INTERVAL_SECONDS,
                    first=FAM_POLL_INTERVAL_SECONDS,
                    data={"kind": "topup", "record_id": tid, "fam_order_id": fam_result["order_id"], "attempts": 0},
                    name=f"fampay_topup_poll_{tid}",
                )
            else:
                logger.warning("Job queue unavailable — FamPay automatic polling disabled; Check Status Now still works.")
            return
        logger.warning(f"FamAPI order create failed for topup #{tid}, falling back — reason: {fam_result.get('error')}")

    zap_result = None
    if get_setting("zapupi_api_key", ""):
        user_row = get_or_create_user(user_id, None, None)
        zap_result = create_zapupi_order(tid, amount, customer_mobile=user_row.get("phone") or "")
        if not zap_result.get("success"):
            logger.warning(f"ZapUPI order create failed for topup #{tid}, falling back to manual QR — reason: {zap_result.get('error')}")

    if zap_result and zap_result.get("success"):
        set_topup_zapupi_order(tid, zap_result["zap_order_id"])
        caption = (
            f"💳 *₹{amount:.0f} ka payment*\n"
            f"Niche 'Pay Now' dabakar kisi bhi UPI app se pay karein.\n\n"
            f"Payment hote hi bot *automatically* wallet update kar dega — kuch bhejna nahi hai. "
            f"Turant confirm karna ho to 'Check Status Now' dabayein."
        )
        buttons = IKM([
            [InlineKeyboardButton("💳 Pay Now", url=zap_result["payment_url"])],
            [InlineKeyboardButton("🔄 Check Status Now", callback_data=f"zapupi_check:{tid}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"qrcancel_topup:{tid}")],
        ])
        if via_query:
            await safe_edit_message_text(update.callback_query, caption, parse_mode="Markdown", reply_markup=buttons)
        else:
            await safe_reply_text(update.message, caption, parse_mode="Markdown", reply_markup=buttons)
        await delete_chat_loading(context.bot, loading_message)

        if context.job_queue is not None:
            context.job_queue.run_repeating(
                zapupi_poll_job,
                interval=ZAPUPI_POLL_INTERVAL_SECONDS,
                first=ZAPUPI_POLL_INTERVAL_SECONDS,
                data={"topup_id": tid, "zap_order_id": zap_result["zap_order_id"], "attempts": 0},
                name=f"zapupi_poll_{tid}",
            )
        else:
            logger.warning("job_queue not available — install python-telegram-bot[job-queue] for ZapUPI auto-polling. 'Check Status Now' still works.")
        return

    # Fallback: own-UPI unique-amount flow — if an SMS Relay Group is set up, this
    # auto-verifies the moment the payment SMS arrives; otherwise it's the same old
    # manual admin-approve flow, just with a unique amount for easy matching.
    pay_amount = reconciliation_amount(amount, tid)
    set_topup_unique_amount(tid, pay_amount)
    order_ref = f"TOPUP{tid:06d}"
    caption = manual_upi_caption(pay_amount, order_ref, extra_note=f"Wallet top-up — credited amount: ₹{amount:.0f}")
    markup = IKM(
        [[InlineKeyboardButton("✅ Maine Payment Kar Diya", callback_data=f"topupdone:{tid}")]]
        + contact_admin_row()
        + [[InlineKeyboardButton("❌ Cancel", callback_data=f"qrcancel_topup:{tid}")]]
    )
    await send_upi_qr(update, context, user_id, pay_amount, via_query=via_query, note=f"{STORE_NAME} {order_ref}", caption=caption, reply_markup=markup)
    await delete_chat_loading(context.bot, loading_message)


async def run_broadcast(context: ContextTypes.DEFAULT_TYPE, admin_chat_id: int, text=None, entities=None, photo_id=None, video_id=None, caption=None, caption_entities=None, source_chat_id=None, source_message_id=None):
    """Broadcast the admin's message to every known user.

    When source_chat_id/source_message_id are supplied, Telegram's copyMessage
    API is used. This is the safest way to preserve the *original Telegram
    message* exactly: already-uploaded media/file_ids, photos, videos, voice,
    audio, documents/files, animations, stickers, links, bold/italic formatting,
    and Premium/custom-emoji entities all travel with the copied message without
    downloading/re-uploading or converting it.

    The older text/photo/video arguments remain supported so existing flows do
    not regress.
    """
    user_ids = list_all_user_ids()
    sent, failed = 0, 0
    for uid in user_ids:
        try:
            if source_chat_id is not None and source_message_id is not None:
                await context.bot.copy_message(
                    chat_id=uid,
                    from_chat_id=source_chat_id,
                    message_id=source_message_id,
                )
            elif photo_id:
                await context.bot.send_photo(chat_id=uid, photo=photo_id, caption=caption, caption_entities=caption_entities)
            elif video_id:
                await context.bot.send_video(chat_id=uid, video=video_id, caption=caption, caption_entities=caption_entities)
            else:
                await context.bot.send_message(chat_id=uid, text=text, entities=entities)
            sent += 1
        except Exception as e:
            logger.warning(f"Broadcast failed for user {uid}: {e}")
            failed += 1
        await asyncio.sleep(0.05)  # stays comfortably under Telegram's rate limits

    try:
        await safe_send_message(context.bot, 
            chat_id=admin_chat_id,
            text=f"📢 *Broadcast complete!*\n✅ Sent: {sent}\n⚠️ Failed (blocked/deleted account): {failed}",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
    except Exception as e:
        logger.warning(f"Could not send broadcast summary to admin {admin_chat_id}: {e}")


async def process_zapupi_status(context: ContextTypes.DEFAULT_TYPE, tid: int, zap_order_id: str):
    """Checks a ZapUPI order once and, if it just resolved, credits the wallet /
    notifies the user. Returns "Success", "Failed", "Pending", or "error" (network/key
    issue — treated like Pending so the safety-net QR flow is never disturbed)."""
    topup = get_topup(tid)
    if not topup or topup["status"] != "pending":
        return topup["status"] if topup else "error"

    result = check_zapupi_order_status(zap_order_id)
    if result.get("error"):
        return "error"
    status = result.get("payment_status")

    if status == "Success":
        set_topup_status(tid, "approved")
        add_wallet_balance(topup["user_id"], topup["amount"])
        new_balance = get_or_create_user(topup["user_id"], None, None)["wallet_balance"]
        try:
            await safe_send_message(context.bot, 
                chat_id=topup["user_id"],
                text=f"🎉 ₹{topup['amount']:.2f} SUCCESSFULLY ADDED BALANCE IN YOUR WALLET BY ADMIN\n\nAny problem? Click Support button.",
                parse_mode="Markdown", reply_markup=payment_success_keyboard(),
            )
        except Exception as e:
            logger.warning(f"Could not notify user {topup['user_id']} of ZapUPI auto-approval: {e}")
        return "Success"

    if status == "Failed":
        set_topup_status(tid, "rejected")
        try:
            await safe_send_message(context.bot, 
                chat_id=topup["user_id"],
                text=f"❌ ₹{topup['amount']:.0f} ka payment fail ya cancel ho gaya. Dobara try karein ya Support se contact karein.",
                reply_markup=back_keyboard(),
            )
        except Exception as e:
            logger.warning(f"Could not notify user {topup['user_id']} of ZapUPI failure: {e}")
        return "Failed"

    return "Pending"


async def zapupi_poll_job(context: ContextTypes.DEFAULT_TYPE):
    """Background job (runs every ZAPUPI_POLL_INTERVAL_SECONDS) that checks a
    pending ZapUPI order and auto-credits the wallet the moment it's paid."""
    job = context.job
    data = job.data
    tid = data["topup_id"]
    zap_order_id = data["zap_order_id"]
    data["attempts"] = data.get("attempts", 0) + 1

    status = await process_zapupi_status(context, tid, zap_order_id)
    if status in ("Success", "Failed"):
        job.schedule_removal()
        return
    if data["attempts"] >= ZAPUPI_POLL_MAX_ATTEMPTS:
        job.schedule_removal()  # stays 'pending' — "Check Status Now" button keeps working any time after this


async def _process_fam_verified_result(context, kind, record_id, record, result):
    """Apply a strict-verified result exactly once; preserve existing wallet/order flow."""
    txn_ref = result.get("utr") or result.get("txn_id") or record.get("fam_order_id")
    if is_transaction_processed(txn_ref):
        return
    expected = record["amount"] if kind == "topup" else record["unique_amount"]
    reported = result.get("amount")
    if reported is not None:
        try:
            if abs(float(reported) - float(expected)) > 0.5:
                logger.warning("FamPay Gateway amount mismatch on %s #%s: expected %s, API said %s", kind, record_id, expected, reported)
                return
        except (TypeError, ValueError):
            pass
    mark_transaction_processed(txn_ref)
    if kind == "topup":
        set_topup_status(record_id, "approved")
        add_wallet_balance(record["user_id"], record["amount"])
        try:
            await safe_send_message(context.bot, chat_id=record["user_id"], text=f"🎉 ₹{record['amount']:.2f} successfully added to your wallet!", reply_markup=payment_success_keyboard())
        except Exception as e:
            logger.warning("Could not notify top-up user %s: %s", record["user_id"], e)
    else:
        if await try_auto_deliver_order(context, record_id):
            return
        await notify_admins(context, f"📦 Order #{record_id} — Payment verified. Product: {record['product_name']}\nPrice: ₹{record['price']:.2f}\nBuyer ID: {record['user_id']}\nPlease deliver the order.", reply_markup=IKM([[InlineKeyboardButton("✅ Confirm & Deliver", callback_data=f"deliver:{record_id}")]]))
        await safe_send_message(context.bot, chat_id=record["user_id"], text="✅ Payment verified. Admin ko notify kar diya hai.", reply_markup=back_keyboard())


async def process_fam_status(context: ContextTypes.DEFAULT_TYPE, kind: str, record_id: int, fam_order_id: str, utr: str):
    """Checks a FamAPI order once and, if it just resolved, credits the wallet
    (topup) or notifies the admin to deliver (order). Duplicate-safe: a given
    txn_id/UTR is only ever allowed to trigger one credit/delivery, no matter how
    many times a poll or a manual 'Check Status' tap runs into it.
    Returns "Success", "Failed", "Pending", or "error" (network issue)."""
    record = get_topup(record_id) if kind == "topup" else get_order(record_id)
    if not record or record["status"] != "pending":
        return record["status"] if record else "error"

    result = await check_fam_order_status(fam_order_id, utr)
    if result.get("error"):
        return "error"
    status = result.get("payment_status")

    if status == "Success":
        txn_ref = result.get("utr") or result.get("txn_id") or fam_order_id
        if is_transaction_processed(txn_ref):
            return "Success"  # already handled by an earlier poll/tap

        expected = record["amount"] if kind == "topup" else record["unique_amount"]
        reported = result.get("amount")
        if reported is not None:
            try:
                if abs(float(reported) - float(expected)) > 0.5:
                    logger.warning(f"FamAPI amount mismatch on {kind} #{record_id}: expected {expected}, API said {reported} — not auto-crediting.")
                    return "Pending"
            except (TypeError, ValueError):
                pass
        mark_transaction_processed(txn_ref)

        if kind == "topup":
            set_topup_status(record_id, "approved")
            add_wallet_balance(record["user_id"], record["amount"])
            new_balance = get_or_create_user(record["user_id"], None, None)["wallet_balance"]
            try:
                await safe_send_message(context.bot, 
                    chat_id=record["user_id"],
                    text=f"🎉 ₹{record['amount']:.2f} SUCCESSFULLY ADDED BALANCE IN YOUR WALLET BY ADMIN\n\nAny problem? Click Support button.",
                    parse_mode="Markdown", reply_markup=payment_success_keyboard(),
                )
            except Exception as e:
                logger.warning(f"Could not notify user {record['user_id']} of FamAPI auto-approval: {e}")
        else:
            if await try_auto_deliver_order(context, record_id):
                return "Success"  # fully auto-delivered — no admin step needed
            admin_text = (
                f"📦 *Order #{record_id} — Payment Auto-Confirmed (FamAPI)*\n"
                f"Product: {record['product_name']}\n"
                f"Price: ₹{record['price']:.0f}\n"
                f"Buyer ID: `{record['user_id']}`\n\n"
                f"Deliver karne ke liye niche tap karein:"
            )
            admin_buttons = IKM([
                [InlineKeyboardButton("✅ Confirm & Deliver", callback_data=f"deliver:{record_id}", style="success")],
                [InlineKeyboardButton("❌ Cancel Order", callback_data=f"cancelorder:{record_id}", style="danger")],
            ])
            await notify_admins(context, admin_text, reply_markup=admin_buttons)
            try:
                await safe_send_message(context.bot, 
                    chat_id=record["user_id"],
                    text="✅ Aapka payment automatically verify ho gaya! Admin turant deliver kar dega.",
                    reply_markup=back_keyboard(),
                )
            except Exception as e:
                logger.warning(f"Could not notify buyer {record['user_id']} of FamAPI auto-approval: {e}")
        return "Success"

    if status == "Failed":
        if kind == "topup":
            set_topup_status(record_id, "rejected")
            fail_text = f"❌ ₹{record['amount']:.0f} ka payment fail ya cancel ho gaya. Dobara try karein ya Support se contact karein."
        else:
            set_order_status(record_id, "cancelled")
            fail_text = "❌ Payment fail ya cancel ho gaya. Dobara try karein ya Support se contact karein."
        try:
            await safe_send_message(context.bot, chat_id=record["user_id"], text=fail_text, reply_markup=back_keyboard())
        except Exception as e:
            logger.warning(f"Could not notify {record['user_id']} of FamAPI failure: {e}")
        return "Failed"

    return "Pending"


async def fam_poll_job(context: ContextTypes.DEFAULT_TYPE):
    """Background job (runs every FAM_POLL_INTERVAL_SECONDS) that checks a pending
    FamAPI order and auto-confirms the moment it's paid. Stops after ~5 minutes if
    still pending (FamAPI's docs don't expose explicit QR-expiry info) and lets the
    buyer know the QR expired."""
    job = context.job
    data = job.data
    kind, record_id, fam_order_id = data["kind"], data["record_id"], data["fam_order_id"]
    data["attempts"] = data.get("attempts", 0) + 1

    status = await process_fam_status(context, kind, record_id, fam_order_id, data.get("utr", ""))
    if status in ("Success", "Failed"):
        job.schedule_removal()
        return
    if data["attempts"] >= FAM_POLL_MAX_ATTEMPTS:
        job.schedule_removal()
        record = get_topup(record_id) if kind == "topup" else get_order(record_id)
        if record and record["status"] == "pending":
            try:
                expired_text, expired_kwargs = send_kwargs_for(
                    render_text_block("order_expired_text", first_name=(record.get("first_name") or "User")),
                    reply_markup=IKM([[InlineKeyboardButton("🔄 Try Again", callback_data="menu:shop" if kind == "order" else "menu:balance")],
                                       [InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")]]),
                )
                await safe_send_message(context.bot, chat_id=record["user_id"], text=expired_text, **expired_kwargs)
            except Exception as e:
                logger.warning(f"Could not notify {record['user_id']} of FamAPI QR expiry: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # If the admin is mid-flow typing free text for something else (e.g. writing
    # a Broadcast Message) and that text happens to include "/start" (e.g. "Type
    # /start to begin"), Telegram routes ANY "/..." message to command handlers
    # first — without this, that text would trigger a real /start here instead of
    # being captured as the broadcast content. Route it back to the right place.
    if context.user_data.get("admin_state"):
        await text_handler(update, context)
        return

    user = update.effective_user
    referred_by = None
    deep_link_product_id = None
    if context.args:
        arg = context.args[0]
        if arg.startswith("prod_"):
            try:
                deep_link_product_id = int(arg[len("prod_"):])
            except ValueError:
                deep_link_product_id = None
        else:
            conn = get_conn()
            row = conn.execute("SELECT user_id FROM users WHERE referral_code = ?", (arg,)).fetchone()
            conn.close()
            if row and row["user_id"] != user.id:
                referred_by = row["user_id"]

    row = get_or_create_user(user.id, user.username, user.first_name, referred_by=referred_by)

    if is_verification_required() and not row.get("verified"):
        if deep_link_product_id:
            context.user_data["pending_deep_link_product"] = deep_link_product_id
        verify_text, verify_kwargs = send_kwargs_for(verify_intro_text(user.first_name), reply_markup=verify_keyboard())
        await safe_reply_text(update.message, verify_text, **verify_kwargs)
        return

    if deep_link_product_id:
        # Product deep links must open the product price list directly.
        # Do not send the normal /start menu first.
        await send_product_detail_message(context, update.effective_chat.id, user.id, deep_link_product_id)
        return

    text, kwargs = send_kwargs_for(welcome_text(user.first_name, row["wallet_balance"]), reply_markup=main_menu_keyboard(user.id))
    await safe_reply_text(update.message, text, **kwargs)


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the contact shared via the Verify Account button."""
    user = update.effective_user
    contact = update.message.contact

    if contact.user_id != user.id:
        await safe_reply_text(update.message, "⚠️ Apna khud ka contact share karein, kisi aur ka nahi.")
        return

    row = get_or_create_user(user.id, user.username, user.first_name)
    already_verified = bool(row.get("verified"))
    set_verified(user.id, contact.phone_number)

    await safe_reply_text(update.message, 
        "✅ Verified! Ab aap shopping shuru kar sakte hain.",
        reply_markup=ReplyKeyboardRemove(),
    )
    pending_pid = context.user_data.pop("pending_deep_link_product", None)
    if pending_pid:
        # After verification, continue straight to the requested product price list.
        await send_product_detail_message(context, update.effective_chat.id, user.id, pending_pid)
    else:
        text, kwargs = send_kwargs_for(welcome_text(user.first_name, row["wallet_balance"]), reply_markup=main_menu_keyboard(user.id))
        await safe_reply_text(update.message, text, **kwargs)

    if not already_verified:
        text = (
            f"🆕 *New Verified User*\n"
            f"Name: {escape_md(user.first_name)}\n"
            f"Username: @{escape_md(user.username) or 'N/A'}\n"
            f"User ID: `{user.id}`\n"
            f"Phone: `{contact.phone_number}`"
        )
        if row.get("referred_by"):
            text += f"\nReferred by: `{row['referred_by']}`"
        await notify_group(context, get_log_group("newuser"), text)

        if row.get("referred_by"):
            ref_text = (
                f"🎁 *New Referral*\n"
                f"Referrer ID: `{row['referred_by']}`\n"
                f"New user: {escape_md(user.first_name)} (`{user.id}`)"
            )
            await notify_group(context, get_log_group("referral"), ref_text)


async def groupid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin utility: send /groupid inside any group to get its numeric chat ID,
    and tap a button to save it as one of the 3 log groups — no code/zip changes needed."""
    if not is_admin(update.effective_user.id):
        return
    chat_id = update.effective_chat.id
    buttons = IKM([
        [InlineKeyboardButton("🆕 Use as New User Log", callback_data=f"setgroup:newuser:{chat_id}")],
        [InlineKeyboardButton("🔑 Use as Key Details Log", callback_data=f"setgroup:keys:{chat_id}")],
        [InlineKeyboardButton("🎁 Use as Referral Log", callback_data=f"setgroup:referral:{chat_id}")],
        [InlineKeyboardButton("📩 Use as SMS Relay Group", callback_data=f"setgroup:smsrelay:{chat_id}")],
    ])
    await safe_reply_text(update.message, f"Group ID: `{chat_id}`\n\nIse kis log ke liye use karna hai?", parse_mode="Markdown", reply_markup=buttons)


def category_buttons():
    """One button per category, each with its own admin-set icon (normal or
    premium/custom emoji) if one's been set — falls back to a plain 📱 otherwise."""
    rows = []
    for cat in list_categories():
        icon = get_category_icon(cat)
        icon_custom_emoji_id = None
        prefix = "📱 "
        if icon:
            if icon.get("icon_custom_emoji_id"):
                icon_custom_emoji_id = icon["icon_custom_emoji_id"]
                prefix = ""
            elif icon.get("icon_emoji"):
                prefix = f"{icon['icon_emoji']} "
        rows.append([InlineKeyboardButton(f"{prefix}{cat}", callback_data=f"shop_cat:{cat}", icon_custom_emoji_id=icon_custom_emoji_id)])
    return rows


async def shop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/shop — quick shortcut to the shop, shown in the bot's ☰ command menu."""
    if context.user_data.get("admin_state"):
        await text_handler(update, context)
        return
    categories = list_categories()
    if not categories:
        await safe_reply_text(update.message, "🛒 Abhi koi product available nahi hai.", reply_markup=back_keyboard())
        return
    buttons = category_buttons()
    buttons.append([InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")])
    text, kwargs = send_kwargs_for(render_text_block("shop_panel_select"), reply_markup=IKM(buttons))
    await safe_reply_text(update.message, text, **kwargs)


async def support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/support — quick shortcut to Support, shown in the bot's ☰ command menu."""
    if context.user_data.get("admin_state"):
        await text_handler(update, context)
        return
    text, entities = render_text_block("support_text")
    admin_username = get_setting("admin_contact_username", "")
    buttons = []
    if admin_username:
        buttons.append([InlineKeyboardButton("CONTACT ADMIN", url=f"https://t.me/{admin_username}")])
    buttons.append([InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")])
    send_text, kwargs = send_kwargs_for((text, entities), reply_markup=IKM(buttons))
    await safe_reply_text(update.message, send_text, **kwargs)


async def send_product_detail_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, pid: int):
    """Open a product directly from its deep link. The message body is ONLY the
    exact saved product description; plan/price buttons remain the purchase controls."""
    loading_message = await send_chat_loading(context.bot, chat_id)
    product = get_product(pid)
    if not product or not product["active"]:
        await safe_send_message(context.bot, chat_id=chat_id, text="Ye product available nahi hai.", reply_markup=back_keyboard())
        await delete_chat_loading(context.bot, loading_message)
        return
    plans = get_plans(pid)
    if not plans:
        await safe_send_message(context.bot, chat_id=chat_id, text="Is product ke koi plans abhi available nahi hain.", reply_markup=back_keyboard())
        await delete_chat_loading(context.bot, loading_message)
        return
    user_is_premium = is_premium(user_id)
    buttons = []
    for pl in plans:
        final_price, _note = compute_final_price(user_id, pl)
        label = f"{pl['label']} — ₹{final_price:.0f}" if user_is_premium and final_price != pl["price"] else f"{pl['label']} — ₹{pl['price']:.0f}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"shop_plan:{pl['id']}")])
    buttons.append([InlineKeyboardButton("⬅ Back to Shop", callback_data="menu:shop")])

    # One fixed admin-editable description is used for every product.
    # Only the live product name and user's first name are substituted.
    text, kwargs = send_kwargs_for(
        render_text_block(
            "shop_product_header",
            product_name=product.get("name") or "Product",
            first_name=(get_user_row(user_id).get("first_name") if get_user_row(user_id) else "User"),
        ),
        reply_markup=IKM(buttons),
    )
    await safe_send_message(context.bot, chat_id=chat_id, text=text, **kwargs)
    await delete_chat_loading(context.bot, loading_message)

def extract_credited_amount(text):
    """Pulls a rupee amount out of a bank/UPI 'payment received' SMS. Returns None
    if the text doesn't look like a credit notification (e.g. it's a debit, or has
    no recognizable amount) — keeps debit/other SMS from ever matching."""
    if not text:
        return None
    lower = text.lower()
    credit_words = ("credited", "credit of", "credit alert", "received", "money received")
    debit_words = ("debited", "debit of", "sent to", "paid to", "withdrawn", "spent at")
    if not any(w in lower for w in credit_words) or any(w in lower for w in debit_words):
        return None
    match = re.search(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)", lower)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


async def sms_relay_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Watches the admin-configured SMS Relay Group for forwarded bank/UPI credit
    SMS (from an SMS-forwarding app on the admin's own phone), matches the amount
    against a pending order/top-up's unique reconciliation amount, and auto-confirms
    it — this is what makes the 'own UPI ID' flow auto-verify without ZapUPI."""
    relay_id = get_setting("sms_relay_group_id", "")
    if not relay_id or update.effective_chat.id != int(relay_id):
        return
    text = update.message.text or update.message.caption or ""
    amount = extract_credited_amount(text)
    if amount is None:
        return

    topup = find_pending_topup_by_amount(amount)
    if topup:
        set_topup_status(topup["id"], "approved")
        add_wallet_balance(topup["user_id"], topup["amount"])
        new_balance = get_or_create_user(topup["user_id"], None, None)["wallet_balance"]
        await safe_reply_text(update.message, f"✅ Matched top-up #{topup['id']} (₹{topup['amount']:.0f}) — wallet auto-credited.")
        try:
            await safe_send_message(context.bot, 
                chat_id=topup["user_id"],
                text=f"🎉 ₹{topup['amount']:.2f} SUCCESSFULLY ADDED BALANCE IN YOUR WALLET BY ADMIN\n\nAny problem? Click Support button.",
                parse_mode="Markdown", reply_markup=payment_success_keyboard(),
            )
        except Exception as e:
            logger.warning(f"Could not notify user {topup['user_id']} of SMS auto-approval: {e}")
        return

    order = find_pending_order_by_amount(amount)
    if order:
        if await try_auto_deliver_order(context, order["id"]):
            await safe_reply_text(update.message, f"✅ Order #{order['id']} ({order['product_name']}) — payment matched, key auto-delivered.")
            return
        await safe_reply_text(update.message, f"✅ Matched order #{order['id']} ({order['product_name']}) — admin ko deliver karne ke liye notify kar diya.")
        admin_text = (
            f"📦 *Order #{order['id']} — Payment Auto-Confirmed (SMS match)*\n"
            f"Product: {order['product_name']}\n"
            f"Price: ₹{order['price']:.0f}\n"
            f"Buyer ID: `{order['user_id']}`\n\n"
            f"Payment SMS se match ho gaya — deliver karne ke liye niche tap karein:"
        )
        admin_buttons = IKM([
            [InlineKeyboardButton("✅ Confirm & Deliver", callback_data=f"deliver:{order['id']}", style="success")],
            [InlineKeyboardButton("❌ Cancel Order", callback_data=f"cancelorder:{order['id']}", style="danger")],
        ])
        await notify_admins(context, admin_text, reply_markup=admin_buttons)
        try:
            await safe_send_message(context.bot, 
                chat_id=order["user_id"],
                text="✅ Aapka payment confirm ho gaya! Admin turant deliver kar dega.",
                reply_markup=back_keyboard(),
            )
        except Exception as e:
            logger.warning(f"Could not notify buyer {order['user_id']} of SMS auto-approval: {e}")
        return


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles a photo sent by the admin while in the 'set product photo' flow
    (Admin Panel → 🖼️ Set Product Photo → pick product → send image) or while
    broadcasting a photo to all users."""
    user_id = update.effective_user.id
    admin_state = context.user_data.get("admin_state")
    if admin_state not in ("setphoto", "broadcast"):
        return  # not an admin-photo flow — ignore, some other handler/nothing applies
    if not is_admin(user_id):
        context.user_data["admin_state"] = None
        return

    if admin_state == "broadcast":
        context.user_data["admin_state"] = None
        file_id = update.message.photo[-1].file_id
        await safe_reply_text(update.message, f"⏳ Broadcast bhej raha hoon {len(list_all_user_ids())} users ko...")
        await run_broadcast(
            context, update.effective_chat.id,
            source_chat_id=update.effective_chat.id,
            source_message_id=update.message.message_id,
        )
        return

    context.user_data["admin_state"] = None
    pid = context.user_data.get("admin_temp", {}).get("product_id")
    product = get_product(pid) if pid else None
    if not product:
        await safe_reply_text(update.message, "⚠️ Product nahi mila, dobara Admin Panel se try karein.", reply_markup=admin_back_keyboard())
        return
    file_id = update.message.photo[-1].file_id  # highest resolution variant
    set_product_photo(pid, file_id)
    await safe_reply_text(update.message, 
        f"✅ *{escape_md(product['name'])}* ki photo set ho gayi.\nAb shop mein ye product photo ke saath, uske price ke sath dikhega.\n\nAage aur configure karna hai?",
        parse_mode="Markdown", reply_markup=product_quick_config_keyboard(pid),
    )


async def broadcast_any_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast any Telegram message type exactly as received.

    This catches media that does not have a dedicated handler above (voice,
    audio, document/file, animation, sticker, video note, etc.). copy_message
    preserves Telegram-native entities and references to media already uploaded
    to Telegram, so the bot never needs to download the file first.
    """
    user_id = update.effective_user.id
    if context.user_data.get("admin_state") != "broadcast":
        return
    if not is_admin(user_id) or not update.message:
        context.user_data["admin_state"] = None
        return
    context.user_data["admin_state"] = None
    await safe_reply_text(
        update.message,
        f"⏳ Broadcast bhej raha hoon {len(list_all_user_ids())} users ko...",
    )
    await run_broadcast(
        context, update.effective_chat.id,
        source_chat_id=update.effective_chat.id,
        source_message_id=update.message.message_id,
    )


async def video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles a video sent by the admin while broadcasting — this is the only
    admin flow that accepts video (product photos stay photo-only)."""
    user_id = update.effective_user.id
    admin_state = context.user_data.get("admin_state")
    if admin_state != "broadcast":
        return
    if not is_admin(user_id):
        context.user_data["admin_state"] = None
        return
    context.user_data["admin_state"] = None
    file_id = update.message.video.file_id
    await safe_reply_text(update.message, f"⏳ Broadcast bhej raha hoon {len(list_all_user_ids())} users ko...")
    await run_broadcast(
        context, update.effective_chat.id,
        source_chat_id=update.effective_chat.id,
        source_message_id=update.message.message_id,
    )


_recent_callback_taps = {}  # user_id -> (last_callback_data, timestamp) — in-memory duplicate-tap guard


async def send_chat_loading(bot, chat_id):
    """Show loading inside the chat for the few genuinely slow operations.
    This intentionally is NOT used for normal button navigation.
    """
    try:
        return await safe_send_message(bot, chat_id=chat_id, text="𝐋𝐨𝐚𝐝𝐢𝐧𝐠......")
    except Exception as e:
        logger.warning(f"Could not send loading message: {e}")
        return None


async def delete_chat_loading(bot, message):
    if not message:
        return
    try:
        await message.delete()
    except Exception:
        try:
            await bot.delete_message(chat_id=message.chat_id, message_id=message.message_id)
        except Exception:
            pass


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # Answer callbacks immediately so Telegram clears its native spinner.
    # Do NOT show a loading toast for normal button navigation.
    await query.answer()
    user = query.from_user
    row = get_or_create_user(user.id, user.username, user.first_name)

    data = query.data

    # Back/navigation from any admin screen is also a real Cancel for any
    # unfinished editable flow. This fixes stale admin_state causing the next
    # command/message to be accidentally saved into the previous field.
    if is_admin(user.id) and (
        data.startswith("admin:")
        or data == "menu:main"
        or data.startswith("btnname_group:")
        or data.startswith("btncolor_group:")
        or data.startswith("descmenu:")
    ):
        clear_admin_input_state(context)

    # Duplicate-tap guard: ignore the exact same button pressed again within ~1.2s
    # by the same user (accidental double-tap before the UI updates) — prevents
    # double orders/payments/etc. Keypad digit taps are exempt since re-tapping
    # the same digit fast on purpose (e.g. "11") is normal, expected input.
    if not (data.startswith("kpadnum:") or data in ("kpadbksp", "kpadclear")):
        now_ts = time.time()
        last = _recent_callback_taps.get(user.id)
        if last and last[0] == data and (now_ts - last[1]) < 1.2:
            return
        _recent_callback_taps[user.id] = (data, now_ts)

    if is_verification_required() and not row.get("verified") and data != "menu:main":
        verify_text, verify_kwargs = send_kwargs_for(verify_intro_text(user.first_name), reply_markup=verify_keyboard())
        await safe_reply_text(query.message, verify_text, **verify_kwargs)
        return

    if data == "menu:main":
        text, kwargs = send_kwargs_for(welcome_text(user.first_name, row["wallet_balance"]), reply_markup=main_menu_keyboard(user.id))
        await safe_edit_message_text(query, text, **kwargs)
        return

    if data == "menu:verify":
        if row.get("verified"):
            await safe_edit_message_text(query, 
                "✅ Aap already verified hain — koi extra step nahi chahiye.",
                reply_markup=back_keyboard(),
            )
        else:
            verify_text, verify_kwargs = send_kwargs_for(verify_intro_text(user.first_name), reply_markup=verify_keyboard())
            await safe_reply_text(query.message, verify_text, **verify_kwargs)
        return

    # ---- Admin Panel ----
    if data == "admin:main":
        if not is_admin(user.id):
            await safe_edit_message_text(query, "⛔ Ye sirf admin ke liye hai.", reply_markup=back_keyboard())
            return
        await safe_edit_message_text(query, "⚙️ *Admin Panel*\nKya karna chahenge?", parse_mode="Markdown", reply_markup=admin_menu_keyboard())
        return

    if data.startswith("admin:") and not is_admin(user.id):
        await safe_edit_message_text(query, "⛔ Ye sirf admin ke liye hai.", reply_markup=back_keyboard())
        return

    if data == "admin:addproduct":
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_temp", None)
        await safe_edit_message_text(query, "➕ *Add Product*\nReseller source chunein:", parse_mode="Markdown", reply_markup=IKM([
            [InlineKeyboardButton("🌐 ADD PRODUCT BANTI SITE", callback_data="admin:addproduct_banti")],
            [InlineKeyboardButton("🌐 ADD PRODUCT ADITYA SITE", callback_data="admin:addproduct_aditya")],
            [InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")],
        ]))
        return

    if data == "admin:addproduct_banti":
        context.user_data["admin_state"] = "addproduct"
        context.user_data["admin_temp"] = {"reseller_source": "banti"}
        await safe_edit_message_text(query, 
            "➕ *Add Product — Banti Site*\nEk line mein bhejein:\n"
            "`Name | Description | Category`\n\n"
            "Example: `Netflix Premium | 1 Screen Shared | Streaming`\n\n"
            "Banti ka existing product configuration same rahega.",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:addproduct_aditya":
        context.user_data.pop("admin_state", None)
        context.user_data["admin_temp"] = {"aditya_catalog_loading": True}
        status = await safe_edit_message_text(query, "⏳ Aditya site se products load ho rahe hain...")
        catalog = await asyncio.to_thread(_aditya_products_sync)
        if not catalog.get("success"):
            await safe_edit_message_text(query, f"❌ {catalog.get('error', 'Aditya products load nahi hue.')}", reply_markup=admin_back_keyboard())
            return
        products = catalog.get("products") or []
        if not products:
            await safe_edit_message_text(query, "❌ Aditya API ne koi product nahi diya.", reply_markup=admin_back_keyboard())
            return
        context.user_data["aditya_catalog"] = products
        rows = []
        for i, ap in enumerate(products[:80]):
            name = str(ap.get("product") or ap.get("name") or ap.get("title") or ap.get("slug") or f"Product {i+1}")
            rows.append([InlineKeyboardButton(name[:60], callback_data=f"aditya_prod:{i}")])
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="admin:addproduct")])
        balance = catalog.get("balance")
        bal_text = f"\n💰 API Balance: {balance}" if balance is not None else ""
        await safe_edit_message_text(query, f"🌐 *ADITYA SITE — PRODUCT CHOOSE KAREIN*{bal_text}", parse_mode="Markdown", reply_markup=IKM(rows))
        return

    if data.startswith("aditya_prod:"):
        try: idx = int(data.split(":", 1)[1])
        except ValueError: idx = -1
        products = context.user_data.get("aditya_catalog") or []
        if idx < 0 or idx >= len(products):
            await safe_edit_message_text(query, "Aditya product list expire ho gayi. Dobara Add Product → Aditya Site karein.", reply_markup=admin_back_keyboard())
            return
        ap = products[idx]
        durations = ap.get("durations") or ap.get("plans") or []
        if isinstance(durations, dict): durations = list(durations.values())
        if not durations:
            await safe_edit_message_text(query, "❌ Is Aditya product ke durations nahi mile.", reply_markup=admin_back_keyboard())
            return
        context.user_data["aditya_selected_product"] = ap
        context.user_data["aditya_durations"] = durations
        name = str(ap.get("product") or ap.get("name") or ap.get("title") or ap.get("slug") or "Aditya Product")
        rows = []
        for i, d in enumerate(durations[:80]):
            dv = d.get("duration") or d.get("days") or d.get("value") or d.get("duration_value") or "?"
            unit = d.get("duration_unit") or d.get("unit") or ("days" if "days" in d else "days")
            price = d.get("effective_price", d.get("price", "?"))
            did = d.get("duration_id") or d.get("id") or d.get("durationId") or "?"
            rows.append([InlineKeyboardButton(f"{dv} {unit} — ₹{price} (ID {did})"[:64], callback_data=f"aditya_dur:{i}")])
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="admin:addproduct_aditya")])
        await safe_edit_message_text(query, f"🌐 *{escape_md(name)}*\nDuration choose karein:", parse_mode="Markdown", reply_markup=IKM(rows))
        return

    if data.startswith("aditya_dur:"):
        try: idx = int(data.split(":", 1)[1])
        except ValueError: idx = -1
        durations = context.user_data.get("aditya_durations") or []
        ap = context.user_data.get("aditya_selected_product") or {}
        if idx < 0 or idx >= len(durations):
            await safe_edit_message_text(query, "Duration list expire ho gayi. Dobara Aditya product select karein.", reply_markup=admin_back_keyboard())
            return
        d = durations[idx]
        remote_pid = ap.get("product_id") or ap.get("id") or ap.get("pid")
        slug = ap.get("slug") or ap.get("product_slug")
        remote_name = str(ap.get("product") or ap.get("name") or ap.get("title") or slug or "Aditya Product")
        duration_id = d.get("duration_id") or d.get("id") or d.get("durationId")
        duration_value = d.get("duration") or d.get("days") or d.get("value") or d.get("duration_value")
        unit = d.get("duration_unit") or d.get("unit") or ap.get("duration_unit") or "days"
        price = d.get("effective_price", d.get("price"))
        if remote_pid is None or duration_id is None or duration_value is None or price is None:
            await safe_edit_message_text(query, "❌ Aditya response mein Product ID / Duration ID / Duration / Price complete nahi mila.", reply_markup=admin_back_keyboard())
            return
        existing = get_aditya_product_by_remote(remote_pid, slug)
        existing_plan = get_aditya_plan_by_duration(existing["id"], duration_id) if existing else None
        context.user_data["admin_state"] = "addproduct_aditya_details"
        context.user_data["admin_temp"] = {
            "reseller_source":"aditya",
            "api_product_id":remote_pid,
            "api_product_slug":slug,
            "remote_name":remote_name,
            "duration_id":duration_id,
            "duration_value":duration_value,
            "duration_unit":unit,
            "api_price":price,
            "existing_product_id": existing["id"] if existing else None,
            "existing_plan_id": existing_plan["id"] if existing_plan else None,
        }
        if existing:
            if existing_plan:
                prompt = (
                    f"⚠️ *{escape_md(remote_name)}* mein ye duration pehle se added hai.\n\n"
                    f"Aditya Cost: `₹{price}`\n"
                    f"Existing Selling Price: `₹{existing_plan['price']}`\n\n"
                    "Naya *Selling Price* bhejein.\n"
                    "Isse sirf isi duration ka customer price update hoga."
                )
            else:
                prompt = (
                    f"✅ *Existing Product Found*\n\nProduct: *{escape_md(remote_name)}*\n"
                    f"Aditya Cost: `₹{price}`\n\n"
                    "Is existing product mein ye naya duration add hoga.\n"
                    "Users ko dikhane wala *Selling Price* bhejein:"
                )
        else:
            prompt = (
                f"✅ *New Aditya Product*\n\nProduct ID: `{remote_pid}`\n"
                f"Product: *{escape_md(remote_name)}*\nSlug: `{slug or '-'}`\n"
                f"Duration ID: `{duration_id}`\nDuration: `{duration_value} {unit}`\n"
                f"Aditya Cost: `₹{price}`\n\n"
                "Apna *Selling Price | Description | Category* bhejein.\n"
                "Example: `70 | My description | Android`"
            )
        await safe_edit_message_text(query, prompt, parse_mode="Markdown", reply_markup=admin_back_keyboard())
        return

    if data == "admin:addplan":
        products = list_products(active_only=False)
        if not products:
            await safe_edit_message_text(query, "Pehle koi product add karein.", reply_markup=admin_back_keyboard())
            return
        buttons = [[InlineKeyboardButton(p["name"], callback_data=f"addplanprod:{p['id']}")] for p in products]
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, "📅 *Add Plan / Duration*\nProduct chunein:", parse_mode="Markdown", reply_markup=IKM(buttons))
        return

    if data.startswith("addplanprod:"):
        pid = int(data.split(":")[1])
        product = get_product(pid)
        if not product:
            await safe_edit_message_text(query, "Ye product nahi mila.", reply_markup=admin_back_keyboard())
            return
        context.user_data["admin_state"] = "addplan"
        context.user_data["admin_temp"] = {"product_id": pid}
        existing_plans = get_plans(pid)
        existing_text = ("\nCurrent plans: " + ", ".join(f"{p['label']}:₹{p['price']:.0f}" for p in existing_plans)) if existing_plans else ""
        await safe_edit_message_text(query, 
            f"📅 *{product['name']}* — Add Plan/Duration\n{existing_text}\n\n"
            f"Format mein bhejein: `Label:Price`\n"
            f"Ek se zyada ek saath bhejne ke liye comma se alag karein.\n\n"
            f"Examples:\n`1 day:80`\n`1 hour:30`\n`1 day:80, 7 days:400, 30 days:1200`",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:removeproduct":
        products = list_products(active_only=True)
        if not products:
            await safe_edit_message_text(query, "Koi active product nahi hai.", reply_markup=admin_back_keyboard())
            return
        buttons = [[InlineKeyboardButton(f"🗑 {p['name']}", callback_data=f"admrm:{p['id']}")] for p in products]
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, "🗑️ *Remove Product*\nRemove karne ke liye tap karein:", parse_mode="Markdown", reply_markup=IKM(buttons))
        return

    if data.startswith("admrm:"):
        pid = int(data.split(":")[1])
        ok = remove_product(pid)
        await safe_edit_message_text(query, "✅ Product permanently delete ho gaya." if ok else "Ye product nahi mili.", reply_markup=admin_back_keyboard())
        return

    if data == "admin:editprice":
        products = list_products(active_only=True)
        if not products:
            await safe_edit_message_text(query, "Koi active product nahi hai.", reply_markup=admin_back_keyboard())
            return
        buttons = [[InlineKeyboardButton(f"✏️ {p['name']}", callback_data=f"adep_prod:{p['id']}")] for p in products]
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, "✏️ *Edit Price*\nProduct chunein:", parse_mode="Markdown", reply_markup=IKM(buttons))
        return

    if data == "admin:editdesc":
        await safe_edit_message_text(query, "📝 *Edit Description*\nEk hi jagah se bot ke saare user-facing descriptions/text edit karein.\n\nPremium/custom emoji, bold aur quote formatting preserve hogi.", parse_mode="Markdown", reply_markup=manage_descriptions_keyboard())
        return

    if data == "descmenu:products":
        products = list_products(active_only=True)
        if not products:
            await safe_edit_message_text(query, "Koi active product nahi hai.", reply_markup=manage_descriptions_keyboard())
            return
        buttons = [[InlineKeyboardButton(f"📝 {p['name']}", callback_data=f"adedesc:{p['id']}")] for p in products]
        buttons.append([InlineKeyboardButton("⬅ Back to Edit Description", callback_data="admin:editdesc")])
        await safe_edit_message_text(query, "📦 *Product Description*\nProduct chunein:", parse_mode="Markdown", reply_markup=IKM(buttons))
        return

    if data.startswith("adedesc:"):
        pid = int(data.split(":")[1])
        context.user_data["admin_state"] = "editdesc"
        context.user_data["admin_temp"] = {"product_id": pid}
        await safe_edit_message_text(
            query,
            "📝 Naya description type karke bhejein — normal ya custom/premium emoji dono use kar sakte hain, sab preserve ho jaayega:",
            reply_markup=admin_back_keyboard(),
        )
        return

    if data.startswith("adep_prod:"):
        pid = int(data.split(":")[1])
        product = get_product(pid)
        plans = get_plans(pid)
        if not plans:
            await safe_edit_message_text(query, "Is product ke koi plans nahi hain.", reply_markup=admin_back_keyboard())
            return
        buttons = [[InlineKeyboardButton(f"{pl['label']} — ₹{pl['price']:.0f}", callback_data=f"adep_plan:{pl['id']}")] for pl in plans]
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, 
            f"✏️ *{product['name']}*\nKaunsa plan edit karna hai?", parse_mode="Markdown", reply_markup=IKM(buttons),
        )
        return

    if data.startswith("adep_plan:"):
        plan_id = int(data.split(":")[1])
        context.user_data["admin_state"] = "editplanprice"
        context.user_data["admin_temp"] = {"plan_id": plan_id}
        await safe_edit_message_text(query, "✏️ Naya price type karke bhejein (sirf number):", reply_markup=admin_back_keyboard())
        return

    if data == "admin:sellingproofs":
        if not is_admin(user.id):
            await safe_edit_message_text(query, "⛔ Ye sirf admin ke liye hai.", reply_markup=back_keyboard())
            return
        enabled = get_setting("selling_proofs_enabled", "1") == "1"
        current = get_setting("selling_proofs_link", "") or "not set"
        main = get_setting("update_channel", "") or "not set"
        await safe_edit_message_text(
            query,
            f"🧾 *Selling Proofs Settings*\n\nStatus: {'ON' if enabled else 'OFF'}\nMain Channel: {main}\nSelling Proofs Channel: {current}\n\n`/sellingproofs` users ke liye command hai. Dono buttons ek hi line mein aayenge.",
            parse_mode="Markdown", reply_markup=selling_proofs_settings_keyboard(),
        )
        return

    if data == "admin:toggle_sellingproofs":
        enabled = get_setting("selling_proofs_enabled", "1") == "1"
        set_setting("selling_proofs_enabled", "0" if enabled else "1")
        enabled = not enabled
        current = get_setting("selling_proofs_link", "") or "not set"
        main = get_setting("update_channel", "") or "not set"
        await safe_edit_message_text(
            query,
            f"🧾 *Selling Proofs Settings*\n\nStatus: {'ON' if enabled else 'OFF'}\nMain Channel: {main}\nSelling Proofs Channel: {current}",
            parse_mode="Markdown", reply_markup=selling_proofs_settings_keyboard(),
        )
        return

    if data == "admin:set_sellingproofs_link":
        context.user_data["admin_state"] = "set_sellingproofs_link"
        current = get_setting("selling_proofs_link", "") or "not set"
        await safe_edit_message_text(
            query,
            f"🔗 Current Selling Proofs Channel: {current}\n\nNaya Telegram channel link bhejein.\nExample: https://t.me/YourChannel\n`off` bhejne se link remove ho jayega.",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:setproofchannel":
        context.user_data["admin_state"] = "setproofchannel"
        current = get_setting("purchase_proof_channel", "not set")
        await safe_edit_message_text(query,
            f"📢 Current Purchase Proof Channel: {current}\n\nNaya channel username/link ya numeric chat ID bhejein.\nExample: @YourChannel or https://t.me/YourChannel\n\nBot ko channel ka admin bana kar post permission dena zaroori hai.\n`off` bhejne par automatic proof posting band ho jayegi.",
            parse_mode="Markdown", reply_markup=admin_back_keyboard())
        return

    if data == "admin:setchannel":
        context.user_data["admin_state"] = "setchannel"
        current = get_setting("update_channel", "not set")
        await safe_edit_message_text(query, f"📢 Current update channel: {current}\nNaya channel link type karein:", reply_markup=admin_back_keyboard())
        return

    if data == "admin:setupi":
        context.user_data["admin_state"] = "setupi"
        current = get_setting("upi_id", "not set")
        await safe_edit_message_text(query, 
            f"💳 Current UPI ID: `{current}`\nNaya UPI ID type karke bhejein (jaise yourname@upi):",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:banti_keys":
        await safe_edit_message_text(query, "🌐 *BANTI API KEY*\nKya change karna hai?", parse_mode="Markdown", reply_markup=IKM([
            [InlineKeyboardButton("🔑 Change Reseller API Key", callback_data="admin:setresellerkey")],
            [InlineKeyboardButton("🔐 Change Master Key", callback_data="admin:setresellermasterkey")],
            [InlineKeyboardButton("⬅ Back", callback_data="admin:main")],
        ]))
        return

    if data == "admin:aditya_keys":
        await safe_edit_message_text(query, "🌐 *ADITYA API KEY*\nAditya API mein sirf `X-Api-Key` required hai.", parse_mode="Markdown", reply_markup=IKM([
            [InlineKeyboardButton("🔑 Change API Key", callback_data="admin:setadityaapikey")],
            [InlineKeyboardButton("⬅ Back", callback_data="admin:main")],
        ]))
        return

    if data == "admin:setadityaapikey":
        context.user_data["admin_state"] = "setadityaapikey"
        current = get_setting("aditya_api_key", "")
        masked = (current[:4] + "..." + current[-4:]) if len(current) > 8 else ("not set" if not current else current)
        await safe_edit_message_text(query, f"🔑 Current Aditya API Key: `{masked}`\n\nNayi Aditya API Key type karke bhejein. `off` bhejne se key remove ho jayegi.", parse_mode="Markdown", reply_markup=admin_back_keyboard())
        return

    if data == "admin:setresellerkey":
        context.user_data["admin_state"] = "setresellerkey"
        current = get_setting("reseller_api_key", "")
        masked = (current[:4] + "..." + current[-4:]) if len(current) > 8 else ("not set" if not current else current)
        await safe_edit_message_text(query, 
            f"🔑 Current Reseller API Key: `{masked}`\nNayi API key type karke bhejein:",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:setresellermasterkey":
        context.user_data["admin_state"] = "setresellermasterkey"
        current = get_setting("reseller_master_key", "")
        masked = (current[:4] + "..." + current[-4:]) if len(current) > 8 else ("not set" if not current else current)
        await safe_edit_message_text(query, 
            f"🔐 Current Reseller Master Key: `{masked}`\n\n"
            f"Ye reseller panel ka `x-master-key` header value hai (API key se alag) — panel ne diya hoga, type karke bhejein. "
            f"Khali bhejne se ye header bhejna band ho jayega.",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:toggleverify":
        new_state = not is_verification_required()
        set_setting("require_phone_verification", "1" if new_state else "0")
        await safe_edit_message_text(query, 
            f"📱 Verify Required ab *{'ON' if new_state else 'OFF'}* hai.\n" +
            ("Naye users ko phone number share karke verify karna hoga bot use karne se pehle." if new_state
             else "Naye users bina phone verify kiye seedha bot use kar sakte hain."),
            parse_mode="Markdown", reply_markup=admin_menu_keyboard(),
        )
        return

    if data == "admin:setzapupikey":
        context.user_data["admin_state"] = "setzapupikey"
        current = get_setting("zapupi_api_key", "")
        masked = (current[:4] + "..." + current[-4:]) if len(current) > 8 else ("not set" if not current else current)
        await safe_edit_message_text(query, 
            f"💳 Current ZapUPI Key: `{masked}`\n\n"
            f"zapupi.com se apni API (zap) key copy karke yahan type karke bhejein — set hote hi 'Add Balance' automatic ban jayega. "
            f"Khali message bhejne se ye off ho jayega aur wapas manual UPI QR + admin-approve flow use hoga.",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:setfamkey":
        context.user_data["admin_state"] = "setfamkey"
        current = get_setting("fam_api_key", "")
        masked = (current[:4] + "..." + current[-4:]) if len(current) > 8 else ("not set" if not current else current)
        await safe_edit_message_text(query, 
            f"💳 Current FAM API Key: `{masked}`\n\n"
            f"FamPay Gateway API key hai. Payment order aur verify dono requests mein ye key use hogi. "
            f"Key set hote hi FamPay Gateway Add Balance/Order payment ke liye sabse pehle try hoga (ZapUPI se pehle). Khali message bhejne se gateway off ho jayega.",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:sethowto":
        context.user_data["admin_state"] = "sethowto"
        current = get_setting("howto_text", "not set")
        await safe_edit_message_text(query, 
            f"📺 Current How To text:\n{current}\n\nNaya How To text type karke bhejein:",
            reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:sethowtovideo":
        context.user_data["admin_state"] = "sethowtovideo"
        current = get_setting("howto_video_url", "not set")
        await safe_edit_message_text(query,
            f"🎬 Current video link: {current}\nNaya Telegram video link type karke bhejein (jaise https://t.me/yourchannel/123):",
            reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:setbotusername":
        context.user_data["admin_state"] = "setbotusername"
        current = get_bot_username()
        await safe_edit_message_text(query,
            f"🤖 Current bot username: {current}\nNaya bot username type karein (@ ke saath ya bina @ ke, dono chalega):",
            reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:setadmincontact":
        context.user_data["admin_state"] = "setadmincontact"
        current = get_setting("admin_contact_username", "not set")
        await safe_edit_message_text(query,
            f"📞 Current admin contact: {current}\nAdmin ka Telegram username type karein (@ ke saath ya bina @ ke, dono chalega):",
            reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:setpremium":
        context.user_data["admin_state"] = "setpremium"
        price = get_setting("premium_price", "99")
        days = get_setting("premium_days", "30")
        await safe_edit_message_text(query, 
            f"👑 *Reseller Settings*\nCurrent: ₹{price} / {days} din\n\n"
            f"Naya format mein bhejein: `Price | Days`\nExample: `99 | 30`",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    # ---- Coupon Codes ----
    if data == "admin:coupons":
        buttons = [
            [InlineKeyboardButton("➕ Create Coupon", callback_data="admin:createcoupon")],
            [InlineKeyboardButton("📋 List Coupons", callback_data="admin:listcoupons")],
            [InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")],
        ]
        await safe_edit_message_text(query, "🎟️ *Coupon Codes*\nKya karna chahenge?", parse_mode="Markdown", reply_markup=IKM(buttons))
        return

    if data == "admin:createcoupon":
        context.user_data["admin_state"] = "createcoupon"
        await safe_edit_message_text(query, 
            "➕ *Create Coupon*\nFormat mein bhejein:\n`CODE | percent/fixed | value | maxuses`\n\n"
            "Examples:\n`SAVE20 | percent | 20 | 0` (20% off, unlimited uses)\n"
            "`FLAT50 | fixed | 50 | 100` (₹50 off, max 100 uses)\n\n"
            "maxuses ke liye 0 likhein unlimited ke liye.",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:listcoupons":
        coupons = list_coupons()
        if not coupons:
            await safe_edit_message_text(query, "🎟️ Koi coupon nahi bana hai abhi tak.", reply_markup=admin_back_keyboard())
            return
        lines = ["🎟️ *All Coupons*\n"]
        buttons = []
        for c in coupons:
            val = f"{c['discount_value']:.0f}%" if c["discount_type"] == "percent" else f"₹{c['discount_value']:.0f}"
            limit = str(c["max_uses"]) if c["max_uses"] is not None else "∞"
            status = "✅" if c["active"] else "⛔"
            lines.append(f"{status} `{c['code']}` — {val} off — used {c['used_count']}/{limit}")
            buttons.append([InlineKeyboardButton(f"❌ Delete {c['code']}", callback_data=f"delcoupon:{c['code']}")])
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, "\n".join(lines), parse_mode="Markdown", reply_markup=IKM(buttons))
        return

    if data.startswith("delcoupon:"):
        code = data.split(":", 1)[1]
        delete_coupon(code)
        coupons = list_coupons()
        if not coupons:
            await safe_edit_message_text(query, f"✅ Coupon `{code}` delete ho gaya.\n\nKoi coupon nahi bacha.", parse_mode="Markdown", reply_markup=admin_back_keyboard())
            return
        lines = [f"✅ Coupon `{code}` delete ho gaya.\n", "🎟️ *All Coupons*\n"]
        buttons = []
        for c in coupons:
            val = f"{c['discount_value']:.0f}%" if c["discount_type"] == "percent" else f"₹{c['discount_value']:.0f}"
            limit = str(c["max_uses"]) if c["max_uses"] is not None else "∞"
            status = "✅" if c["active"] else "⛔"
            lines.append(f"{status} `{c['code']}` — {val} off — used {c['used_count']}/{limit}")
            buttons.append([InlineKeyboardButton(f"❌ Delete {c['code']}", callback_data=f"delcoupon:{c['code']}")])
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, "\n".join(lines), parse_mode="Markdown", reply_markup=IKM(buttons))
        return

    if data == "admin:addadmin":
        context.user_data["admin_state"] = "addadmin"
        await safe_edit_message_text(query, "👑 Naye admin ka Telegram numeric user ID type karke bhejein:", reply_markup=admin_back_keyboard())
        return

    if data == "admin:listadmins":
        lines = [f"• {a} (config)" for a in ADMIN_IDS]
        lines += [f"• {a['user_id']} (added {time.strftime('%d %b %Y', time.localtime(a['added_at']))})" for a in list_admins_db()]
        await safe_edit_message_text(query, "📋 *Admins*\n" + "\n".join(lines), parse_mode="Markdown", reply_markup=admin_back_keyboard())
        return

    if data == "admin:removeadmin":
        dynamic_admins = list_admins_db()
        if not dynamic_admins:
            await safe_edit_message_text(query, 
                "Koi bhi admin bot se add nahi kiya gaya hai — sirf config wale admin hain (`ADMIN_IDS`), unhe yahan se remove nahi kiya ja sakta, sirf code file mein.",
                parse_mode="Markdown", reply_markup=admin_back_keyboard(),
            )
            return
        rows = [[InlineKeyboardButton(f"🗑️ {a['user_id']}", callback_data=f"deladmin:{a['user_id']}")] for a in dynamic_admins]
        rows.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, 
            "🗑️ *Remove Admin*\nJisko hatana hai use tap karein (sirf bot se add kiye gaye admins yahan dikhte hain):",
            parse_mode="Markdown", reply_markup=IKM(rows),
        )
        return

    if data.startswith("deladmin:"):
        uid = int(data.split(":", 1)[1])
        removed = remove_admin_db(uid)
        dynamic_admins = list_admins_db()
        if not dynamic_admins:
            await safe_edit_message_text(query, 
                f"✅ Admin `{uid}` remove ho gaya." if removed else "Ye admin pehle se hi nahi mila.",
                parse_mode="Markdown", reply_markup=admin_back_keyboard(),
            )
            return
        rows = [[InlineKeyboardButton(f"🗑️ {a['user_id']}", callback_data=f"deladmin:{a['user_id']}")] for a in dynamic_admins]
        rows.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, 
            f"✅ Admin `{uid}` remove ho gaya.\n\nBaki admins:" if removed else "Ye admin pehle se hi nahi mila.",
            parse_mode="Markdown", reply_markup=IKM(rows),
        )
        return

    if data == "admin:addreseller":
        context.user_data["admin_state"] = "addreseller"
        await safe_edit_message_text(query,
            "👑 *Add Reseller*\nJis user ko Reseller banana hai uska Telegram *numeric User ID* ya *@username* bhejein.\n\n"
            "Example: `123456789` ya `@username`",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:removereseller":
        context.user_data["admin_state"] = "removereseller"
        await safe_edit_message_text(query,
            "🚫 *Remove Reseller*\nJis user ka Reseller (Premium) status hatana hai uska Telegram *numeric User ID* ya *@username* bhejein.\n\n"
            "Example: `123456789` ya `@username`",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:addbalanceuser":
        context.user_data["admin_state"] = "addbalanceuser"
        await safe_edit_message_text(query,
            "💰 *Add / Remove User Balance*\n\n"
            "Format bhejein:\n"
            "`+20 123456789` → ₹20 add\n"
            "`-20 123456789` → ₹20 minus\n\n"
            "Username bhi use kar sakte hain:\n"
            "`+20 @username` ya `-20 @username`",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    # ---- Button Colors (Bot API 9.4 style field: primary/success/danger) ----
    if (data.startswith("btncolor_group:") or data.startswith("btncolor_pick:") or data.startswith("btncolor_set:")) and not is_admin(user.id):
        await safe_edit_message_text(query, "⛔ Ye sirf admin ke liye hai.", reply_markup=back_keyboard())
        return

    if data == "admin:buttoncolors":
        await safe_edit_message_text(query, 
            "🎨 *Button Colors*\nEk group chunein — usme jitne bhi buttons hain, sabka color yahan se set kar sakte ho:",
            parse_mode="Markdown", reply_markup=button_color_groups_keyboard(),
        )
        return

    if data.startswith("btncolor_group:"):
        group = data.split(":", 1)[1]
        await safe_edit_message_text(query, 
            f"🎨 {group}\nJis button ka color badalna hai use tap karein:",
            reply_markup=button_color_list_keyboard(group),
        )
        return

    if data.startswith("btncolor_pick:"):
        key = data.split(":", 1)[1]
        label = BUTTON_LABELS.get(key, key)
        await safe_edit_message_text(query, 
            f"🎨 {label}\nColor chunein:",
            reply_markup=button_color_picker_keyboard(key),
        )
        return

    if data.startswith("btncolor_set:"):
        # key itself can contain ":" (e.g. "menu:orders", "payplan:wallet"),
        # so split off the color code from the right, not the left.
        key, code = data[len("btncolor_set:"):].rsplit(":", 1)
        set_button_style(key, code)
        label = BUTTON_LABELS.get(key, key)
        color_label = next((l for c, l, _s in BUTTON_COLOR_CHOICES if c == code), code)
        await safe_edit_message_text(query, 
            f"✅ {label} ab {color_label} hai.",
            reply_markup=button_color_picker_keyboard(key),
        )
        return

    # ---- Edit Button Name (rename + premium/custom emoji per button) ----
    if (data.startswith("btnname_group:") or data.startswith("btnname_pick:") or data.startswith("btnname_reset:") or data == "admin:buttonnames") and not is_admin(user.id):
        await safe_edit_message_text(query, "⛔ Ye sirf admin ke liye hai.", reply_markup=back_keyboard())
        return

    if data == "admin:backmenuicon":
        current = get_button_label_override("nav:back_menu") or {}
        current_icon = current.get("icon_custom_emoji_id") or current.get("icon_emoji") or "default"
        await safe_edit_message_text(
            query,
            "🔣 *Back to Menu Icon*\n\n"
            "Normal emoji bhejein (jaise 👈 🏠 🔙) ya Telegram Premium/custom emoji bhejein.\n"
            "Sirf icon save hoga — Back to Menu ka naam alag se change kar sakte ho.\n\n"
            f"Current icon: {current_icon}",
            parse_mode="Markdown",
            reply_markup=IKM([[InlineKeyboardButton("🔄 Reset Icon", callback_data="admin:backmenuicon_reset")],
                              [InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")]]),
        )
        context.user_data["admin_state"] = "setbackmenuicon"
        context.user_data["admin_temp"] = {}
        return

    if data == "admin:backmenuicon_reset":
        current = get_button_label_override("nav:back_menu") or {}
        set_button_label_override(
            "nav:back_menu",
            label=current.get("label"),
            icon_emoji=None,
            icon_custom_emoji_id=None,
        )
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_temp", None)
        await safe_edit_message_text(query, "✅ Back to Menu ka icon reset ho gaya.", reply_markup=admin_back_keyboard())
        return

    if data == "admin:buttonnames":
        await safe_edit_message_text(query, 
            "✏️ *Edit Button Name*\nEk group chunein — usme jitne bhi buttons hain, unka naam/emoji yahan se badal sakte ho:",
            parse_mode="Markdown", reply_markup=button_name_groups_keyboard(),
        )
        return

    if data.startswith("btnname_group:"):
        group = data.split(":", 1)[1]
        await safe_edit_message_text(query, 
            f"✏️ {group}\nJis button ka naam/emoji badalna hai use tap karein:",
            reply_markup=button_name_list_keyboard(group),
        )
        return

    if data.startswith("btnname_pick:"):
        key = data.split(":", 1)[1]
        label = BUTTON_LABELS.get(key, key)
        group = next((g for k, _l, g, _d in BUTTON_REGISTRY if k == key), None)
        if group == "Item Lists":
            prompt = (
                f"✏️ {label}\n"
                "Ye button har item (product/order/etc.) ke liye alag-alag text ke sath repeat hota hai, "
                "isliye iska poora naam change nahi ho sakta.\n\n"
                "Lekin aap ek emoji bhej sakte ho jo icon ki tarah in sabhi buttons pe lag jayega:\n"
                "• Normal emoji (🔥🎯🛒) — free, kisi ko bhi kaam karega\n"
                "• Premium/custom emoji — sirf tab dikhega agar bot banane wale ka Telegram account Premium hai"
            )
        else:
            prompt = (
                f"✏️ {label}\n"
                "Naya button text type karke bhejein (normal emoji bhi sath likh sakte ho, jaise: 🔥 Shop Now).\n\n"
                "Agar sirf ek premium/custom emoji bhi bhejna hai, to wo bhi type karke bhej sakte ho — "
                "wo icon ki tarah button pe lag jayega (sirf premium account par dikhega)."
            )
        context.user_data["admin_state"] = "editbuttonname"
        context.user_data["admin_temp"] = {"button_key": key, "group": group}
        await safe_edit_message_text(query, prompt, reply_markup=button_name_prompt_keyboard(key, group))
        return

    if data.startswith("btnname_reset:"):
        key = data.split(":", 1)[1]
        reset_button_label_override(key)
        label = BUTTON_LABELS.get(key, key)
        group = next((g for k, _l, g, _d in BUTTON_REGISTRY if k == key), None)
        await safe_edit_message_text(query, 
            f"✅ {label} default naam/emoji par reset ho gaya.",
            reply_markup=button_name_list_keyboard(group),
        )
        return

    # ---- Manage Buttons (show/hide existing menu buttons + add/remove custom ones) ----
    if (data.startswith("togglehide:") or data.startswith("hidebtn_group:") or data.startswith("addcustombtn_type:") or data.startswith("delcustombtn:")
            or data in ("admin:managebuttons", "admin:hidebuttons", "admin:addcustombtn", "admin:removecustombtn")) and not is_admin(user.id):
        await safe_edit_message_text(query, "⛔ Ye sirf admin ke liye hai.", reply_markup=back_keyboard())
        return

    if data == "admin:managebuttons":
        await safe_edit_message_text(query, 
            "🧩 *Manage Buttons*\nBot ke kisi bhi button ko hide/show karein (koi bhi screen ka), ya apne khud ke naye button add/remove karein:",
            parse_mode="Markdown", reply_markup=manage_buttons_keyboard(),
        )
        return

    if data == "admin:hidebuttons":
        await safe_edit_message_text(query, 
            "👁️ *Show/Hide Buttons*\nPehle group chunein — us group ke andar jo bhi button hai use hide/show kar sakte ho:",
            parse_mode="Markdown", reply_markup=hide_buttons_groups_keyboard(),
        )
        return

    if data.startswith("hidebtn_group:"):
        group = data.split(":", 1)[1]
        note = "\n⚠️ Ye group har item (product/order/etc.) pe repeat hone wale buttons hai — hide karne se sab jagah se gayab ho jayega." if group == "Item Lists" else ""
        note += "\n⚠️ Ye har screen ka Back button hai — hide karne se navigation mushkil ho sakta hai." if group == "Navigation" else ""
        await safe_edit_message_text(query, 
            f"👁️ *{group}*\nTap karke on/off karein (👁️ = dikh raha hai, 🚫 = hidden):{note}",
            parse_mode="Markdown", reply_markup=hide_buttons_list_keyboard(group),
        )
        return

    if data.startswith("togglehide:"):
        key = data.split(":", 1)[1]
        set_button_hidden(key, not is_button_hidden(key))
        group = next((g for k, _l, g, _d in BUTTON_REGISTRY if k == key), None)
        await safe_edit_message_text(query, 
            f"👁️ *{group}*\nTap karke on/off karein (👁️ = dikh raha hai, 🚫 = hidden):",
            parse_mode="Markdown", reply_markup=hide_buttons_list_keyboard(group),
        )
        return

    if data == "admin:addcustombtn":
        context.user_data["admin_state"] = "addcustombtn_label"
        await safe_edit_message_text(query, 
            "➕ *Add Custom Button*\nButton ka naam type karke bhejein (emoji bhi laga sakte ho), jaise: `🎮 Join Discord`",
            parse_mode="Markdown", reply_markup=manage_buttons_keyboard(),
        )
        return

    if data.startswith("addcustombtn_type:"):
        action_type = data.split(":", 1)[1]
        context.user_data.setdefault("admin_temp", {})["action_type"] = action_type
        context.user_data["admin_state"] = "addcustombtn_value"
        if action_type == "url":
            prompt = "🔗 Ab URL bhejein jahan ye button le jaayega, jaise: `https://t.me/AMANOFCUPDATES`"
        else:
            prompt = "💬 Ab wo text message type karke bhejein jo is button ko tap karne par user ko dikhega:"
        await safe_edit_message_text(query, prompt, parse_mode="Markdown", reply_markup=manage_buttons_keyboard())
        return

    if data == "admin:removecustombtn":
        buttons = list_custom_buttons()
        if not buttons:
            await safe_edit_message_text(query, "Koi custom button abhi add nahi kiya gaya.", reply_markup=manage_buttons_keyboard())
            return
        await safe_edit_message_text(query, 
            "🗑️ *Remove Custom Button*\nJisko hatana hai use tap karein:",
            parse_mode="Markdown", reply_markup=remove_custom_buttons_keyboard(),
        )
        return

    if data.startswith("delcustombtn:"):
        cbid = int(data.split(":", 1)[1])
        remove_custom_button(cbid)
        await safe_edit_message_text(query, "✅ Button remove ho gaya.", reply_markup=remove_custom_buttons_keyboard())
        return

    if data.startswith("custombtn:"):
        cbid = int(data.split(":", 1)[1])
        cb = get_custom_button(cbid)
        if not cb:
            await safe_edit_message_text(query, "Ye button ab available nahi hai.", reply_markup=back_keyboard())
            return
        await safe_edit_message_text(query, cb["action_value"], reply_markup=back_keyboard())
        return

    # ---- Manage Descriptions (any text block, anywhere in the bot) ----
    if (data == "admin:managedescriptions" or data.startswith("descblock:") or data.startswith("descreset:")) and not is_admin(user.id):
        await safe_edit_message_text(query, "⛔ Ye sirf admin ke liye hai.", reply_markup=back_keyboard())
        return

    if data == "admin:managedescriptions":
        await safe_edit_message_text(query, "📝 *Edit Description*", parse_mode="Markdown", reply_markup=manage_descriptions_keyboard())
        return

    if data.startswith("descblock:"):
        key = data.split(":", 1)[1]
        label = TEXT_BLOCK_LABELS.get(key, key)
        default_template = TEXT_BLOCK_DEFAULTS.get(key, "")
        placeholder_note = ""
        if "{" in default_template:
            placeholder_note = "\n\n⚠️ Jo bhi { } wale placeholder is default text mein hain (jaise {price}, {category}), unhe apne naye text mein bhi waise hi rakhna — wo tap-time par asli value se replace ho jaate hain."
        context.user_data["admin_state"] = "editTextBlock"
        context.user_data["admin_temp"] = {"block_key": key}
        await safe_edit_message_text(query, 
            f"📝 *{label}*\nAbhi ka default:\n`{default_template}`\n\nNaya text type karke bhejein (normal ya custom/premium emoji dono use kar sakte hain):{placeholder_note}",
            parse_mode="Markdown", reply_markup=description_prompt_keyboard(key),
        )
        return

    if data.startswith("descreset:"):
        key = data.split(":", 1)[1]
        reset_text_block(key)
        label = TEXT_BLOCK_LABELS.get(key, key)
        await safe_edit_message_text(query, f"✅ {label} default text par reset ho gaya.", reply_markup=manage_descriptions_keyboard())
        return

    if data == "admin:setcategoryicon":
        categories = list_categories()
        if not categories:
            await safe_edit_message_text(query, "Koi category nahi hai.", reply_markup=admin_back_keyboard())
            return
        rows = []
        for cat in categories:
            icon = get_category_icon(cat)
            mark = "✏️ " if icon and (icon.get("icon_emoji") or icon.get("icon_custom_emoji_id")) else ""
            rows.append([InlineKeyboardButton(f"{mark}{cat}", callback_data=f"admcaticon:{cat}")])
        rows.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, "🗂️ *Set Category Icon*\nJis category ka icon set karna hai use chunein:", parse_mode="Markdown", reply_markup=IKM(rows))
        return

    if data.startswith("admcaticon:"):
        cat = data.split(":", 1)[1]
        context.user_data["admin_state"] = "setcategoryicon"
        context.user_data["admin_temp"] = {"category": cat}
        await safe_edit_message_text(query, 
            f"🗂️ *{escape_md(cat)}*\nAb ek emoji bhejein:\n"
            f"• Normal emoji (📱🔓🍎) — free, kisi ko bhi kaam karega\n"
            f"• Custom/premium emoji — sirf tab kaam karega agar bot banane wale account par Telegram Premium hai",
            parse_mode="Markdown", reply_markup=IKM([[InlineKeyboardButton("⬅ Back", callback_data="admin:setcategoryicon")]]),
        )
        return

    if data == "admin:ordering":
        await safe_edit_message_text(query, "🔢 *Product / Category Order*\n\nProducts ko select karke unki category ke andar position 1, 2, 3... set karein. Categories ke liye bhi same system hai.", parse_mode="Markdown", reply_markup=product_order_list_keyboard())
        return

    if data == "admin:categoryorder":
        await safe_edit_message_text(query, "🗂️ *Category Order*\n\nJis category ka number change karna hai use chunein:", parse_mode="Markdown", reply_markup=category_order_keyboard())
        return

    if data.startswith("admproductorder:"):
        pid = int(data.split(":", 1)[1])
        product = get_product(pid)
        if not product:
            await safe_edit_message_text(query, "Product nahi mila.", reply_markup=admin_back_keyboard()); return
        await safe_edit_message_text(query, f"🔢 *{escape_md(product['name'])}*\nCategory: `{escape_md(product.get('category') or 'General')}`\n\nPosition choose karein:", parse_mode="Markdown", reply_markup=product_order_positions_keyboard(pid))
        return

    if data.startswith("setprodorder:"):
        _, pid_s, pos_s = data.split(":", 2)
        pid, pos = int(pid_s), int(pos_s)
        if set_product_order(pid, pos):
            product = get_product(pid)
            await safe_edit_message_text(query, f"✅ *{escape_md(product['name'])}* ab apni category mein position *{get_product_order(pid)}* par hai.", parse_mode="Markdown", reply_markup=product_order_list_keyboard())
        else:
            await safe_edit_message_text(query, "❌ Product order set nahi ho saka.", reply_markup=product_order_list_keyboard())
        return

    if data.startswith("setcatorder:"):
        category = unquote(data.split(":", 1)[1])
        await safe_edit_message_text(query, f"🗂️ *{escape_md(category)}*\n\nIs category ko kis number par rakhna hai?", parse_mode="Markdown", reply_markup=category_order_positions_keyboard(category))
        return

    if data.startswith("move_cat:"):
        parts = data.split(":")
        category = unquote(parts[1])
        pos = int(parts[2])
        if set_category_order(category, pos):
            await safe_edit_message_text(query, f"✅ *{escape_md(category)}* ab category list mein position *{list_categories().index(category)+1}* par hai.", parse_mode="Markdown", reply_markup=category_order_keyboard())
        else:
            await safe_edit_message_text(query, "❌ Category order set nahi ho saka.", reply_markup=category_order_keyboard())
        return

    if data == "admin:productlink":
        products = list_products(active_only=True)
        if not products:
            await safe_edit_message_text(query, "Koi active product nahi hai.", reply_markup=admin_back_keyboard())
            return
        await safe_edit_message_text(query, "🔗 *Get Product Link*\nProduct chunein:", parse_mode="Markdown", reply_markup=product_link_list_keyboard())
        return

    if data.startswith("productlink:"):
        pid = int(data.split(":", 1)[1])
        product = get_product(pid)
        if not product:
            await safe_edit_message_text(query, "Product nahi mila.", reply_markup=admin_back_keyboard())
            return
        bot_username = get_bot_username().lstrip("@")
        product_link = f"https://t.me/{bot_username}?start=prod_{pid}"
        text = (
            f"🔗 *{escape_md(product['name'])}*\n\n"
            f"Product Link:\n{product_link}\n\n"
            f"Is link ko customer ko send karke product directly open karwa sakte hain."
        )
        await safe_edit_message_text(query, text, parse_mode="Markdown", reply_markup=product_link_result_keyboard(pid, product_link))
        return

    if data == "admin:manageproducts":
        products = list_products(active_only=False)
        if not products:
            await safe_edit_message_text(query, "Koi product nahi hai.", reply_markup=admin_back_keyboard())
            return
        buttons = [[InlineKeyboardButton(f"{'🚫 ' if not p['active'] else ''}{p['name']}", callback_data=f"admproductcfg:{p['id']}")] for p in products]
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, "📦 *Products*\nJis product ko configure karna hai use chunein:", parse_mode="Markdown", reply_markup=IKM(buttons))
        return

    if data.startswith("admproductcfg:"):
        pid = int(data.split(":")[1])
        product = get_product(pid)
        if not product:
            await safe_edit_message_text(query, "Ye product nahi mila.", reply_markup=admin_back_keyboard())
            return
        plans = get_plans(pid)
        plans_summary = ", ".join(f"{pl['label']} ₹{pl['price']:.0f}" for pl in plans) if plans else "Koi plan nahi hai"
        text = (
            f"📦 *{escape_md(product['name'])}*\n"
            f"Delivery: {product['delivery_mode']}\n"
            f"PID: `{product.get('api_product_code') or 'not set'}`\n"
            f"Plans: {plans_summary}\n"
            f"Channel: {product.get('channel_link') or 'Not set'}\n"
            f"Maintenance: {'🛠️ ON' if product.get('maintenance') else '✅ OFF'}\n"
            f"Status: {'✅ Active' if product['active'] else '🚫 Deactivated'}\n\n"
            f"Configure karne ke liye niche se chunein:"
        )
        await safe_edit_message_text(query, text, parse_mode="Markdown", reply_markup=product_quick_config_keyboard(pid))
        return

    if data == "admin:maintenance":
        products = list_products(active_only=False)
        if not products:
            await safe_edit_message_text(query, "Koi product nahi hai.", reply_markup=admin_back_keyboard())
            return
        buttons = []
        for p in products:
            status = "🛠️ ON" if p.get("maintenance") else "✅ OFF"
            buttons.append([InlineKeyboardButton(f"{status} {p['name']}", callback_data=f"admmaintenance:{p['id']}")])
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(
            query,
            "🛠️ *Product Maintenance*\n\nJis product ko maintenance par dalna ya hatana hai use chunein:",
            parse_mode="Markdown", reply_markup=IKM(buttons),
        )
        return

    if data.startswith("admmaintenance:"):
        if not is_admin(user.id):
            await safe_edit_message_text(query, "⛔ Ye sirf admin ke liye hai.", reply_markup=back_keyboard())
            return
        pid = int(data.split(":")[1])
        product = get_product(pid)
        if not product:
            await safe_edit_message_text(query, "Product nahi mila.", reply_markup=admin_back_keyboard())
            return
        enabled = not bool(product.get("maintenance"))
        set_product_maintenance(pid, enabled)
        product = get_product(pid) or product
        channel = product.get("channel_link") or "Not set"
        status = "🛠️ ON" if product.get("maintenance") else "✅ OFF"
        await safe_edit_message_text(
            query,
            f"🛠️ *{escape_md(product['name'])}*\n\n"
            f"Maintenance: {status}\n"
            f"Product Channel: {channel}\n\n"
            "Maintenance ON hone par customer ko admin-editable maintenance description aur "
            "product channel ka CHECK UPDATES button dikhega.",
            parse_mode="Markdown",
            reply_markup=IKM([
                [InlineKeyboardButton("🔄 Toggle Maintenance", callback_data=f"admmaintenance:{pid}")],
                [InlineKeyboardButton("📦 Open Product Settings", callback_data=f"admproductcfg:{pid}")],
                [InlineKeyboardButton("⬅ Back to Maintenance List", callback_data="admin:maintenance")],
            ]),
        )
        return

    if data == "admin:setdelivery":
        products = list_products(active_only=True)
        if not products:
            await safe_edit_message_text(query, "Koi active product nahi hai.", reply_markup=admin_back_keyboard())
            return
        buttons = [[InlineKeyboardButton(f"{p['name']} (abhi: {p['delivery_mode']})", callback_data=f"admdel:{p['id']}")] for p in products]
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, "🔌 *Delivery Mode*\nProduct chunein:", parse_mode="Markdown", reply_markup=IKM(buttons))
        return

    if data == "admin:setpid":
        products = list_products(active_only=True)
        if not products:
            await safe_edit_message_text(query, "Koi active product nahi hai.", reply_markup=admin_back_keyboard())
            return
        buttons = [[InlineKeyboardButton(f"{p['name']} (PID: {p['api_product_code'] or 'not set'})", callback_data=f"admsetpid:{p['id']}")] for p in products]
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, "🔢 *Set Product PID*\nProduct chunein:", parse_mode="Markdown", reply_markup=IKM(buttons))
        return

    if data.startswith("admsetpid:"):
        pid = int(data.split(":")[1])
        context.user_data["admin_state"] = "setpid"
        context.user_data["admin_temp"] = {"product_id": pid}
        await safe_edit_message_text(
            query,
            "🔢 Reseller site (`xyzcheats.com`) ka is product ka **PID number** type karke bhejein (jaise `133`):",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:seticon":
        products = list_products(active_only=True)
        if not products:
            await safe_edit_message_text(query, "Koi active product nahi hai.", reply_markup=admin_back_keyboard())
            return
        buttons = [[InlineKeyboardButton(f"{p['icon_emoji'] or ''} {p['name']}".strip(), callback_data=f"admicon:{p['id']}")] for p in products]
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, "🎨 *Set Product Icon*\nProduct chunein:", parse_mode="Markdown", reply_markup=IKM(buttons))
        return

    if data.startswith("admicon:"):
        pid = int(data.split(":")[1])
        context.user_data["admin_state"] = "seticon"
        context.user_data["admin_temp"] = {"product_id": pid}
        await safe_edit_message_text(query, 
            "🎨 Ab ek emoji bhejein:\n"
            "• Normal emoji (🔫🎯🛡️) — free, kisi ko bhi kaam karega\n"
            "• Custom/uploaded emoji — sirf tab kaam karega agar aapka (bot banane wala) Telegram account Premium hai",
            reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:setphoto":
        products = list_products(active_only=True)
        if not products:
            await safe_edit_message_text(query, "Koi active product nahi hai.", reply_markup=admin_back_keyboard())
            return
        buttons = [[InlineKeyboardButton(f"{'🖼️ ' if p.get('photo_file_id') else ''}{p['name']}", callback_data=f"admphoto:{p['id']}")] for p in products]
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, "🖼️ *Set Product Photo*\nProduct chunein:", parse_mode="Markdown", reply_markup=IKM(buttons))
        return

    if data.startswith("admchannel:"):
        pid = int(data.split(":")[1])
        product = get_product(pid)
        if not product:
            await safe_edit_message_text(query, "Product nahi mila.", reply_markup=admin_back_keyboard())
            return
        context.user_data["admin_state"] = "setproductchannel"
        context.user_data["admin_temp"] = {"product_id": pid}
        current = product.get("channel_link") or "Not set"
        await safe_edit_message_text(query,
            f"📢 *{escape_md(product['name'])}*\nCurrent channel: {current}\n\nNaya Telegram channel link bhejein.\nExample: `https://t.me/YourChannel`\nBand karna ho to `off` bhejein.",
            parse_mode="Markdown", reply_markup=product_quick_config_keyboard(pid))
        return

    if data.startswith("admphoto:"):
        pid = int(data.split(":")[1])
        context.user_data["admin_state"] = "setphoto"
        context.user_data["admin_temp"] = {"product_id": pid}
        await safe_edit_message_text(query, 
            "🖼️ Ab is product ki photo (image) bhejein — ye shop mein product ke naam aur price ke sath dikhegi:",
            reply_markup=admin_back_keyboard(),
        )
        return

    if data.startswith("admdel:"):
        pid = int(data.split(":")[1])
        buttons = IKM([
            [InlineKeyboardButton("🔧 Manual (admin khud bhejega)", callback_data=f"setdel:manual:{pid}")],
            [InlineKeyboardButton("🌐 API (auto reseller se)", callback_data=f"setdel:api:{pid}")],
            [InlineKeyboardButton("⬅ Back", callback_data="admin:setdelivery")],
        ])
        await safe_edit_message_text(query, "Mode chunein:", reply_markup=buttons)
        return

    if data == "admin:setapiduration":
        products = list_products(active_only=True)
        if not products:
            await safe_edit_message_text(query, "Koi active product nahi hai.", reply_markup=admin_back_keyboard())
            return
        buttons = [[InlineKeyboardButton(p["name"], callback_data=f"admapidur_prod:{p['id']}")] for p in products]
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, 
            "⏱️ *Set Plan API Duration*\nProduct chunein — fir uske jis plan ka reseller-API-wala duration text set karna hai wo chunenge:",
            parse_mode="Markdown", reply_markup=IKM(buttons),
        )
        return

    if data.startswith("admapidur_prod:"):
        pid = int(data.split(":")[1])
        await safe_edit_message_text(query, "⏱️ Plan chunein:", reply_markup=api_duration_plan_list_keyboard(pid))
        return

    if data.startswith("admapidur_plan:"):
        plid = int(data.split(":")[1])
        plan = get_plan(plid)
        context.user_data["admin_state"] = "setapiduration"
        context.user_data["admin_temp"] = {"plan_id": plid, "product_id": plan["product_id"] if plan else None}
        back_cb = f"admapidur_prod:{plan['product_id']}" if plan else "admin:setapiduration"
        await safe_edit_message_text(query, 
            "⏱️ Reseller panel (xyzcheats.com) ke dashboard mein is duration ke liye jo *exact* text likha hai "
            "(jaise `1 Day`, `3 Hours`, `7 Days` — spelling/capital-small sab exact match) wahi yahan type karke bhejein.\n\n"
            "Ye customer ko dikhne wale plan naam se alag ho sakta hai — sirf reseller API ko yahi wala bheja jayega.",
            parse_mode="Markdown", reply_markup=IKM([[InlineKeyboardButton("⬅ Back", callback_data=back_cb)]]),
        )
        return

    if data == "admin:setresellerprice":
        products = list_products(active_only=True)
        if not products:
            await safe_edit_message_text(query, "Koi active product nahi hai.", reply_markup=admin_back_keyboard())
            return
        buttons = [[InlineKeyboardButton(p["name"], callback_data=f"admresprice_prod:{p['id']}")] for p in products]
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, 
            "👑 *Fix Price for Resellers*\nProduct chunein — fir jis plan ka reseller price fix karna hai wo chunenge:",
            parse_mode="Markdown", reply_markup=IKM(buttons),
        )
        return

    if data.startswith("admresprice_prod:"):
        pid = int(data.split(":")[1])
        await safe_edit_message_text(query, "👑 Plan chunein:", reply_markup=reseller_price_plan_list_keyboard(pid))
        return

    if data.startswith("admresprice_plan:"):
        plid = int(data.split(":")[1])
        plan = get_plan(plid)
        context.user_data["admin_state"] = "setresellerprice"
        context.user_data["admin_temp"] = {"plan_id": plid, "product_id": plan["product_id"] if plan else None}
        back_cb = f"admresprice_prod:{plan['product_id']}" if plan else "admin:setresellerprice"
        current = f" (abhi: ₹{plan['reseller_price']:.0f})" if plan and plan.get("reseller_price") is not None else ""
        await safe_edit_message_text(query, 
            f"👑 *{escape_md(plan['label']) if plan else ''}*{current}\nNormal price: ₹{plan['price']:.0f}\n\n"
            f"Resellers ke liye is plan ki fixed price type karke bhejein (sirf number, jaise `70`).\n"
            f"Reset karne ke liye `off` bhejein — tab reseller bhi normal price hi dekhega.",
            parse_mode="Markdown", reply_markup=IKM([[InlineKeyboardButton("⬅ Back", callback_data=back_cb)]]),
        )
        return

    if data.startswith("setdel:"):
        _, mode, pid_str = data.split(":")
        pid = int(pid_str)
        if mode == "manual":
            set_product_delivery(pid, "manual", None)
            await safe_edit_message_text(query, "✅ Ye product ab *Manual* delivery par set hai.\n\nAage aur configure karna hai?", parse_mode="Markdown", reply_markup=product_quick_config_keyboard(pid))
            return
        context.user_data["admin_state"] = "setdelivery_apicode"
        context.user_data["admin_temp"] = {"product_id": pid}
        await safe_edit_message_text(query, 
            "🌐 Reseller panel ka *product code/ID* type karke bhejein:",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data.startswith("setandroid:"):
        _, requires_str, pid_str = data.split(":")
        pid = int(pid_str)
        set_requires_android_id(pid, requires_str == "1")
        msg = "✅ Is product ke liye Android ID zaroori set kar diya." if requires_str == "1" else "✅ Is product ke liye Android ID zaroori nahi hai."
        await safe_edit_message_text(query, msg + "\n\nAage aur configure karna hai?", reply_markup=product_quick_config_keyboard(pid))
        return

    if data == "admin:broadcast":
        total = len(list_all_user_ids())
        context.user_data["admin_state"] = "broadcast"
        await safe_edit_message_text(query, 
            f"📢 *Broadcast Message*\nAbhi total *{total}* users hain.\n\n"
            f"Jo bhi message bhejein — text, photo, ya video (caption ke saath) — wahi sabko bheja jayega, "
            f"exact wahi format/emoji ke saath jo aap yahan bhejenge.",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:smsguide":
        current_upi = get_setting("upi_id", "not set")
        relay_id = get_setting("sms_relay_group_id", "not set")
        await safe_edit_message_text(query, 
            "📩 *SMS Auto-Verify Setup*\n"
            "Apni khud ki UPI ID se payments bina kisi gateway ke automatically verify karne ke steps:\n\n"
            f"1️⃣ Apni UPI ID set karein (abhi: `{current_upi}`) — Admin Panel → 💳 Change UPI ID\n\n"
            "2️⃣ Ek naya Telegram *group* banayein (sirf aapke liye, private), aur is bot ko usme *admin* bana dein "
            "(zaroori hai, warna privacy mode ki wajah se bot messages nahi padh payega)\n\n"
            "3️⃣ Us group mein `/groupid` bhejein → *📩 Use as SMS Relay Group* button dabayein\n\n"
            "4️⃣ Apne phone mein koi bhi SMS-forwarding app install karein (jaise Macrodroid, Automate, Tasker) "
            "jo bank ka payment-credited SMS is Telegram group mein forward kar de\n\n"
            f"Abhi ka SMS Relay Group: `{relay_id}`\n\n"
            "Jaise hi koi payment-credited SMS group mein aayega, bot amount match karke order/top-up automatically "
            "confirm kar dega — na ZapUPI chahiye, na admin approval.",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin:pendingorders":
        orders = list_pending_orders()
        if not orders:
            await safe_edit_message_text(query, "📦 Koi pending order nahi hai.", reply_markup=admin_back_keyboard())
            return
        buttons = [[InlineKeyboardButton(f"#{o['id']} {o['product_name']} — ₹{o['price']:.0f}", callback_data=f"vieworder:{o['id']}")] for o in orders]
        buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")])
        await safe_edit_message_text(query, "📦 *Pending Orders*", parse_mode="Markdown", reply_markup=IKM(buttons))
        return

    if data.startswith("vieworder:"):
        oid = int(data.split(":")[1])
        order = get_order(oid)
        if not order:
            await safe_edit_message_text(query, "Order nahi mila.", reply_markup=admin_back_keyboard())
            return
        buyer = get_or_create_user(order["user_id"], None, None)
        text = (
            f"📦 *Order #{oid}*\n"
            f"Product: {order['product_name']}\n"
            f"Price: ₹{order['price']:.0f}\n"
            f"Buyer ID: `{order['user_id']}`\n"
            f"Status: {order['status']}"
        )
        if order.get("android_id"):
            text += f"\nAndroid ID: `{order['android_id']}`"
        if order.get("api_status"):
            text += f"\nLast API attempt: {order['api_status'][:200]}"
        buttons = IKM([
            [InlineKeyboardButton("✅ Confirm & Deliver", callback_data=f"deliver:{oid}", style="success")],
            [InlineKeyboardButton("❌ Cancel Order", callback_data=f"cancelorder:{oid}", style="danger")],
            [InlineKeyboardButton("⬅ Back", callback_data="admin:pendingorders")],
        ])
        await safe_edit_message_text(query, text, parse_mode="Markdown", reply_markup=buttons)
        return

    if data.startswith("cancelorder:"):
        oid = int(data.split(":")[1])
        set_order_status(oid, "cancelled")
        await safe_edit_message_text(query, f"❌ Order #{oid} cancel ho gaya.", reply_markup=admin_back_keyboard())
        return

    if data.startswith("deliver:"):
        oid = int(data.split(":")[1])
        order = get_order(oid)
        if not order or order["status"] != "pending":
            await safe_edit_message_text(query, "Ye order ab pending nahi hai.", reply_markup=admin_back_keyboard())
            return
        product = get_product(order["product_id"])

        if product and product.get("delivery_mode") == "api" and product.get("api_product_code"):
            if product.get("requires_android_id"):
                context.user_data["admin_state"] = "delivering_order_android_id"
                context.user_data["admin_temp"] = {"order_id": oid}
                await safe_edit_message_text(query, "📱 Is product ke liye customer ka *Android ID* chahiye (Support se le lein). Type karke bhejein:", parse_mode="Markdown")
                return

            duration = get_order_api_duration(order)
            await safe_edit_message_text(query, "⏳ Reseller API se key fetch ho rahi hai...")
            result = await buy_key_for_product(product, order, oid)
            if result.get("success"):
                key_text = result["key"]
                set_order_status(oid, "delivered")
                set_order_delivery_info(oid, delivered_key=key_text, api_status="success")
                receipt_text, receipt_kwargs = send_kwargs_for(format_delivery_receipt(order, key_text), reply_markup=delivery_receipt_keyboard(order))
                await safe_send_message(context.bot, chat_id=order["user_id"], text=receipt_text, **receipt_kwargs)
                await post_key_to_log_group(context, order, key_text)
                await safe_edit_message_text(query, f"✅ Order #{oid} API se deliver ho gaya.", reply_markup=admin_back_keyboard())
            else:
                set_order_delivery_info(oid, delivered_key=None, api_status=f"failed: {result.get('error')}"[:500])
                buttons = IKM([
                    [InlineKeyboardButton("✏️ Manual Deliver Karein", callback_data=f"delivermanual:{oid}")],
                    [InlineKeyboardButton("⬅ Back", callback_data="admin:pendingorders")],
                ])
                await safe_edit_message_text(query, f"⚠️ API fail ho gaya:\n{result.get('error')}\n\nManual deliver karna chahenge?", reply_markup=buttons)
            return

        context.user_data["admin_state"] = "delivering_order"
        context.user_data["admin_temp"] = {"order_id": oid}
        await safe_edit_message_text(query, "✏️ Customer ko bhejni wali key/text type karke bhejein:", reply_markup=admin_back_keyboard())
        return

    if data.startswith("delivermanual:"):
        oid = int(data.split(":")[1])
        context.user_data["admin_state"] = "delivering_order"
        context.user_data["admin_temp"] = {"order_id": oid}
        await safe_edit_message_text(query, "✏️ Customer ko bhejni wali key/text type karke bhejein:", reply_markup=admin_back_keyboard())
        return

    if data.startswith("setgroup:"):
        if not is_admin(user.id):
            await safe_edit_message_text(query, "⛔ Ye sirf admin ke liye hai.", reply_markup=back_keyboard())
            return
        _, kind, chat_id = data.split(":")
        if kind == "smsrelay":
            set_setting("sms_relay_group_id", chat_id)
            await safe_edit_message_text(query, 
                "✅ Ye group ab *SMS Relay Group* hai.\n\n"
                "Ab is group mein jo bhi 'payment received/credited ₹X' jaisa SMS-forward text aayega, "
                "bot usme se amount nikalke pending orders/top-ups se match karega aur auto-confirm kar dega.",
                parse_mode="Markdown",
            )
            return
        set_setting(f"log_group_{kind}", chat_id)
        labels = {"newuser": "New User Log", "keys": "Key Details Log", "referral": "Referral Log"}
        await safe_edit_message_text(query, f"✅ Ye group ab *{labels[kind]}* ke liye set ho gaya hai.", parse_mode="Markdown")
        return

    if data == "menu:shop":
        categories = list_categories()
        if not categories:
            await safe_edit_message_text(query, "🛒 Abhi koi product available nahi hai.", reply_markup=back_keyboard())
            return
        buttons = category_buttons()
        buttons.append([InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")])
        text, kwargs = send_kwargs_for(render_text_block("shop_panel_select"), reply_markup=IKM(buttons))
        await safe_edit_message_text(query, text, **kwargs)
        return

    if data.startswith("shop_cat:"):
        category = data.split(":", 1)[1]
        products = list_products_by_category(category)
        if not products:
            await safe_edit_message_text(query, "Is panel mein abhi koi product nahi hai.", reply_markup=back_keyboard())
            return
        product_buttons = []
        for p in products:
            if p.get("icon_custom_emoji_id"):
                btn = InlineKeyboardButton(p["name"], callback_data=f"shop_prod:{p['id']}", icon_custom_emoji_id=p["icon_custom_emoji_id"])
            elif p.get("icon_emoji"):
                btn = InlineKeyboardButton(f"{p['icon_emoji']} {p['name']}", callback_data=f"shop_prod:{p['id']}")
            else:
                btn = InlineKeyboardButton(p["name"], callback_data=f"shop_prod:{p['id']}")
            product_buttons.append(btn)
        buttons = [[b] for b in product_buttons]
        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="menu:shop")])
        text, kwargs = send_kwargs_for(
            render_text_block(
                "shop_category_header",
                category=category,
                first_name=user.first_name or "User",
            ),
            reply_markup=IKM(buttons),
        )
        await safe_edit_message_text(query, text, **kwargs)
        return

    if data.startswith("shop_prod:"):
        pid = int(data.split(":")[1])
        product = get_product(pid)
        if not product or not product["active"]:
            await safe_edit_message_text(query, "Ye product available nahi hai.", reply_markup=back_keyboard())
            return

        if product.get("maintenance"):
            send_text, kwargs = send_kwargs_for(
                render_text_block(
                    "product_maintenance_text",
                    Product_name=product.get("name") or "Product",
                    product_name=product.get("name") or "Product",
                )
            )
            channel = maintenance_updates_url(product.get("channel_link"))
            rows = []
            if channel:
                rows.append([InlineKeyboardButton("CHECK UPDATES", url=channel)])
            rows.append([InlineKeyboardButton("⬅ Back", callback_data=f"shop_cat:{product['category']}")])
            kwargs["reply_markup"] = IKM(rows)
            await safe_edit_message_text(query, send_text, **kwargs)
            return

        plans = get_plans(pid)
        if not plans:
            await safe_edit_message_text(query, "Is product ke koi plans abhi available nahi hain.", reply_markup=back_keyboard())
            return
        user_is_premium = is_premium(user.id)
        buttons = []
        for pl in plans:
            final_price, _note = compute_final_price(user.id, pl)
            label = f"{pl['label']} — ₹{final_price:.0f}" if user_is_premium and final_price != pl["price"] else f"{pl['label']} — ₹{pl['price']:.0f}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"shop_plan:{pl['id']}")])
        buttons.append([InlineKeyboardButton("⬅ Back", callback_data=f"shop_cat:{product['category']}")])

        # All products share one fixed admin-editable description.
        # The product name is injected automatically for this price-list screen.
        send_text, kwargs = send_kwargs_for(
            render_text_block(
                "shop_product_header",
                product_name=product.get("name") or "Product",
                first_name=user.first_name or "User",
            ),
            reply_markup=IKM(buttons),
        )
        await safe_edit_message_text(query, send_text, **kwargs)
        return

    if data.startswith("shop_plan:"):
        plan_id = int(data.split(":")[1])
        plan = get_plan(plan_id)
        if not plan:
            await safe_edit_message_text(query, "Ye plan available nahi hai.", reply_markup=back_keyboard())
            return
        product = get_product(plan["product_id"])
        text, markup, entities = build_plan_payment_screen(user, plan, product, context)
        await safe_edit_message_text(query, text, entities=entities, reply_markup=markup)
        return

    if data.startswith("applycoupon:"):
        plan_id = int(data.split(":")[1])
        context.user_data["awaiting_coupon_for_plan"] = plan_id
        await safe_edit_message_text(query, "🎟️ Coupon code type karke bhejein:", reply_markup=IKM([[InlineKeyboardButton("⬅ Back", callback_data=f"shop_plan:{plan_id}")]]))
        return

    if data.startswith("removecoupon:"):
        plan_id = int(data.split(":")[1])
        context.user_data.setdefault("applied_coupons", {}).pop(str(plan_id), None)
        plan = get_plan(plan_id)
        if not plan:
            await safe_edit_message_text(query, "Ye plan available nahi hai.", reply_markup=back_keyboard())
            return
        product = get_product(plan["product_id"])
        text, markup, entities = build_plan_payment_screen(user, plan, product, context)
        await safe_edit_message_text(query, text, entities=entities, reply_markup=markup)
        return

    if data.startswith("payplan:"):
        _, method, plan_id_str = data.split(":")
        plan_id = int(plan_id_str)
        plan = get_plan(plan_id)
        if not plan:
            await safe_edit_message_text(query, "Ye plan available nahi hai.", reply_markup=back_keyboard())
            return
        product = get_product(plan["product_id"])
        final_price, discount_note = compute_final_price(user.id, plan)
        order_name = f"{product['name']} ({plan['label']})"

        # Apply coupon (if user attached one to this plan) at the moment of payment too,
        # so it can't be double-applied and always matches what was shown on screen.
        applied_coupons = context.user_data.setdefault("applied_coupons", {})
        applied_code = applied_coupons.get(str(plan_id))
        coupon_used = None
        if applied_code:
            coupon = get_coupon(applied_code)
            valid, _err = coupon_is_valid(coupon)
            if valid:
                final_price, discount_amt = apply_coupon_discount(final_price, coupon)
                discount_note += f"🎟️ Coupon {applied_code} applied: -₹{discount_amt:.0f}\n"
                coupon_used = applied_code
            else:
                applied_coupons.pop(str(plan_id), None)

        if method == "wallet":
            row = get_or_create_user(user.id, user.username, user.first_name)
            if row["wallet_balance"] < final_price:
                await safe_edit_message_text(query, "❌ Wallet balance kam ho gaya hai. UPI se pay karein.", reply_markup=back_keyboard())
                return
            deduct_wallet_balance(user.id, final_price)
            oid = create_order(user.id, product["id"], order_name, final_price, status="pending", plan_id=plan_id)
            if coupon_used:
                increment_coupon_usage(coupon_used)
                applied_coupons.pop(str(plan_id), None)
            await safe_edit_message_text(query, 
                f"✅ *₹{final_price:.0f} wallet se deduct ho gaya!*\nOrder #{oid} place ho gaya — delivery ka wait karein.",
                parse_mode="Markdown", reply_markup=back_keyboard(),
            )
            if await try_auto_deliver_order(context, oid):
                return  # API delivery mode — key already sent, no admin step needed
            admin_text = (
                f"📦 *New Order #{oid} — Wallet Paid*\n"
                f"Product: {order_name}\n"
                f"Price: ₹{final_price:.0f}\n"
                f"Buyer: {escape_md(user.first_name)} (@{escape_md(user.username) or 'N/A'}, `{user.id}`)\n\n"
                f"Deliver karne ke liye niche tap karein:"
            )
            admin_buttons = IKM([
                [InlineKeyboardButton("✅ Confirm & Deliver", callback_data=f"deliver:{oid}", style="success")],
                [InlineKeyboardButton("❌ Cancel Order", callback_data=f"cancelorder:{oid}", style="danger")],
            ])
            await notify_admins(context, admin_text, reply_markup=admin_buttons)
            return

        # method == "upi"
        oid = create_order(user.id, product["id"], order_name, final_price, status="pending", plan_id=plan_id)
        if coupon_used:
            increment_coupon_usage(coupon_used)
            applied_coupons.pop(str(plan_id), None)
        order_ref = f"ORD{oid:06d}"

        if get_setting("fam_api_key", ""):
            fam_result = await create_fam_order(oid, final_price)
            if fam_result.get("success"):
                set_order_fam_order(oid, fam_result["order_id"])
                set_order_unique_amount(oid, final_price)
                caption = (
                    f"💳 *₹{final_price:.0f} ka payment*\n{order_name}\n{discount_note}\n"
                    f"QR scan karein ya Pay Now dabayein.\n\n"
                    f"Payment ke baad FamPay Gateway status automatically check karega. Confirm hone par admin ko delivery ke liye notify kiya jayega."
                ).rstrip()
                fam_rows = []
                if fam_result.get("payment_link"):
                    fam_rows.append([InlineKeyboardButton("💳 Pay Now", url=fam_result["payment_link"] )])
                fam_rows.extend([
                    [InlineKeyboardButton("🔄 Check Status Now", callback_data=f"fam_check:order:{oid}")],
                    [InlineKeyboardButton("❌ Cancel", callback_data=f"qrcancel_order:{oid}")],
                ])
                fam_buttons = IKM(fam_rows)
                try:
                    await query.delete_message()
                except Exception:
                    pass
                if fam_result.get("qr_url"):
                    await safe_send_photo(context.bot, chat_id=query.message.chat_id, photo=fam_result["qr_url"],
                                          caption=caption, parse_mode="Markdown", reply_markup=fam_buttons)
                else:
                    await safe_send_message(context.bot, chat_id=query.message.chat_id,
                                            text=caption + "\n\nQR/checkout kholne ke liye Pay Now dabayein.", parse_mode="Markdown", reply_markup=fam_buttons)
                if context.job_queue is not None:
                    context.job_queue.run_repeating(
                        fam_poll_job,
                        interval=FAM_POLL_INTERVAL_SECONDS,
                        first=FAM_POLL_INTERVAL_SECONDS,
                        data={"kind": "order", "record_id": oid, "fam_order_id": fam_result["order_id"], "attempts": 0},
                        name=f"fampay_order_poll_{oid}",
                    )
                else:
                    logger.warning("Job queue unavailable — FamPay automatic polling disabled; Check Status Now still works.")
                return
            logger.warning(f"FamAPI order create failed for order #{oid}, falling back — reason: {fam_result.get('error')}")

        pay_amount = reconciliation_amount(final_price, oid)
        set_order_unique_amount(oid, pay_amount)
        caption = manual_upi_caption(pay_amount, order_ref, extra_note=f"{order_name}\n{discount_note}".rstrip())
        note = f"{STORE_NAME} {order_ref}"
        qr_markup = IKM(
            [[InlineKeyboardButton("✅ Verify Payment", callback_data=f"verifypay:{oid}", style="success")]]
            + contact_admin_row()
            + [[InlineKeyboardButton("❌ Cancel", callback_data=f"qrcancel_order:{oid}")]]
        )
        await send_upi_qr(update, context, user.id, pay_amount, via_query=True, note=note, caption=caption, reply_markup=qr_markup)
        return

    if data == "menu:balance":
        # Opening Add Balance must always exit UTR-entry mode. The pending payment
        # remains pending unless the user explicitly cancels it with /cancel or the
        # inline Cancel button, so a menu tap never silently loses a payment record.
        context.user_data.pop("awaiting_fam_utr", None)
        context.user_data["topup_kpad_amount"] = ""
        topup_text, topup_entities = topup_keypad_text("", row["wallet_balance"])
        topup_text, topup_kwargs = send_kwargs_for((topup_text, topup_entities), reply_markup=topup_amount_buttons())
        await safe_edit_message_text(query, topup_text, **topup_kwargs)
        return

    if data.startswith("topup:"):
        val = data.split(":")[1]
        if val == "custom":
            context.user_data["topup_kpad_amount"] = ""
            topup_text, topup_entities = topup_keypad_text("", row["wallet_balance"])
            topup_text, topup_kwargs = send_kwargs_for((topup_text, topup_entities), reply_markup=topup_keypad_keyboard("", row["wallet_balance"]))
            await safe_edit_message_text(query, topup_text, **topup_kwargs)
            return
        amount = float(val)
        await start_wallet_topup(update, context, user.id, amount, via_query=True)
        return

    if data.startswith("kpadnum:"):
        digit = data.split(":", 1)[1]
        amount_str = context.user_data.get("topup_kpad_amount", "")
        if amount_str == "0":
            amount_str = digit  # replace leading zero instead of stacking onto it
        else:
            amount_str += digit
        if len(amount_str) > 7:  # matches the ₹50,000 practical ceiling, avoids absurd numbers
            amount_str = amount_str[:7]
        context.user_data["topup_kpad_amount"] = amount_str
        topup_text, topup_entities = topup_keypad_text(amount_str, row["wallet_balance"])
        topup_text, topup_kwargs = send_kwargs_for((topup_text, topup_entities), reply_markup=topup_keypad_keyboard(amount_str, row["wallet_balance"]))
        await safe_edit_message_text(query, topup_text, **topup_kwargs)
        return

    if data == "kpadclear":
        context.user_data["topup_kpad_amount"] = ""
        topup_text, topup_entities = topup_keypad_text("", row["wallet_balance"])
        topup_text, topup_kwargs = send_kwargs_for((topup_text, topup_entities), reply_markup=topup_keypad_keyboard("", row["wallet_balance"]))
        await safe_edit_message_text(query, topup_text, **topup_kwargs)
        return

    if data == "kpadbksp":
        amount_str = context.user_data.get("topup_kpad_amount", "")[:-1]
        context.user_data["topup_kpad_amount"] = amount_str
        topup_text, topup_entities = topup_keypad_text(amount_str, row["wallet_balance"])
        topup_text, topup_kwargs = send_kwargs_for((topup_text, topup_entities), reply_markup=topup_keypad_keyboard(amount_str, row["wallet_balance"]))
        await safe_edit_message_text(query, topup_text, **topup_kwargs)
        return

    if data == "kpadback":
        context.user_data.pop("topup_kpad_amount", None)
        text, kwargs = send_kwargs_for(welcome_text(user.first_name, row["wallet_balance"]), reply_markup=main_menu_keyboard(user.id))
        await safe_edit_message_text(query, text, **kwargs)
        return

    if data == "kpadconfirm":
        amount_str = context.user_data.get("topup_kpad_amount", "")
        min_amt, max_amt = topup_min_max()
        amount = float(amount_str) if amount_str else 0
        if amount < min_amt or amount > max_amt:
            await query.answer(f"⚠️ Amount ₹{min_amt:,.0f} se ₹{max_amt:,.0f} ke beech hona chahiye.", show_alert=True)
            return
        context.user_data.pop("topup_kpad_amount", None)
        await start_wallet_topup(update, context, user.id, amount, via_query=True)
        return

    if data.startswith("qrcancel_order:"):
        oid = int(data.split(":")[1])
        order = get_order(oid)
        if order and order["user_id"] == user.id and order["status"] == "pending":
            set_order_status(oid, "cancelled")
        cancel_markup = IKM([[InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:shop")]])
        cancel_text, cancel_kwargs = send_kwargs_for(
            render_text_block("order_cancelled_text", first_name=user.first_name),
            reply_markup=cancel_markup,
        )
        await safe_edit_message_caption(query, cancel_text, **cancel_kwargs)
        return

    if data.startswith("verifypay:"):
        oid = int(data.split(":")[1])
        order = get_order(oid)
        if not order or order["user_id"] != user.id:
            await safe_edit_message_caption(query, "Ye order nahi mila.", reply_markup=back_keyboard())
            return
        if order["status"] != "pending":
            await safe_edit_message_caption(query, f"Ye order already {order['status']} ho chuka hai.", reply_markup=back_keyboard())
            return
        text, entities = render_text_block("qr_verify_pending", price=f"{order['price']:.0f}")
        cap_kwargs = {"caption_entities": entities} if entities is not None else {"parse_mode": "Markdown"}
        await safe_edit_message_caption(query, text, reply_markup=back_keyboard(), **cap_kwargs)
        admin_text = (
            f"📦 *Order #{oid} — Payment Verify Request (UPI)*\n"
            f"Product: {order['product_name']}\n"
            f"Price: ₹{order['price']:.0f}\n"
            f"Buyer: {escape_md(user.first_name)} (@{escape_md(user.username) or 'N/A'}, `{user.id}`)\n\n"
            f"Payment screenshot/UTR Support mein check karke, deliver karne ke liye niche tap karein:"
        )
        admin_buttons = IKM([
            [InlineKeyboardButton("✅ Confirm & Deliver", callback_data=f"deliver:{oid}", style="success")],
            [InlineKeyboardButton("❌ Cancel Order", callback_data=f"cancelorder:{oid}", style="danger")],
        ])
        await notify_admins(context, admin_text, reply_markup=admin_buttons)
        return

    if data.startswith("qrcancel_topup:"):
        tid = int(data.split(":")[1])
        topup = get_topup(tid)
        if topup and topup["user_id"] == user.id and topup["status"] == "pending":
            set_topup_status(tid, "cancelled")
        cancel_markup = IKM([[InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:balance")]])
        cancel_text, cancel_kwargs = send_kwargs_for(
            render_text_block("order_cancelled_text", first_name=user.first_name),
            reply_markup=cancel_markup,
        )
        await safe_edit_message_caption(query, cancel_text, **cancel_kwargs)
        return

    if data.startswith("zapupi_check:"):
        tid = int(data.split(":")[1])
        topup = get_topup(tid)
        if not topup or topup["user_id"] != user.id:
            await safe_edit_message_caption(query, "Ye request nahi mili.", reply_markup=back_keyboard())
            return
        if topup["status"] != "pending":
            await safe_edit_message_caption(query, f"Ye request already {topup['status']} ho chuki hai.", reply_markup=back_keyboard())
            return
        zap_order_id = topup.get("zapupi_order_id")
        if not zap_order_id:
            await safe_edit_message_caption(query, "⚠️ ZapUPI order info nahi mila, Support se contact karein.", reply_markup=back_keyboard())
            return
        status = await process_zapupi_status(context, tid, zap_order_id)
        if status == "Success":
            await safe_edit_message_caption(query, "✅ Payment confirm ho gaya! Wallet update ho gaya hai.", reply_markup=back_keyboard())
        elif status == "Failed":
            await safe_edit_message_caption(query, "❌ Payment fail ho gaya. Dobara try karein ya Support se contact karein.", reply_markup=back_keyboard())
        else:
            retry_buttons = IKM([
                [InlineKeyboardButton("🔄 Check Status Now", callback_data=f"zapupi_check:{tid}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"qrcancel_topup:{tid}")],
            ])
            await safe_edit_message_caption(query, 
                f"⏳ *₹{topup['amount']:.0f}* ka payment abhi tak confirm nahi hua — thodi der baad dobara 'Check Status Now' dabayein, ya UPI app se payment status check karein.",
                parse_mode="Markdown", reply_markup=retry_buttons,
            )
        return

    if data.startswith("fam_utr:"):
        _, kind, rid_str = data.split(":")
        rid = int(rid_str)
        record = get_topup(rid) if kind == "topup" else get_order(rid)
        if not record or record["user_id"] != user.id or record["status"] != "pending":
            await safe_edit_message_caption(query, "Ye pending payment request nahi mili.", reply_markup=back_keyboard())
            return
        if not record.get("fam_order_id"):
            await safe_edit_message_caption(query, "⚠️ Payment order ID nahi mila. Support se contact karein.", reply_markup=back_keyboard())
            return
        context.user_data["awaiting_fam_utr"] = {"kind": kind, "record_id": rid}
        await safe_send_message(context.bot, chat_id=user.id,
            text="Apne payment ka 12-digit UTR number bhejein. UTR sirf payment complete hone ke baad bhejein. Cancel karne ke liye /cancel bhejein.",
            reply_markup=back_keyboard())
        return

    if data.startswith("fam_check:"):
        _, kind, rid_str = data.split(":")
        rid = int(rid_str)
        record = get_topup(rid) if kind == "topup" else get_order(rid)
        if not record or record["user_id"] != user.id:
            await safe_edit_message_caption(query, "Ye request nahi mili.", reply_markup=back_keyboard())
            return
        if record["status"] != "pending":
            await safe_edit_message_caption(query, f"Ye request already {record['status']} ho chuki hai.", reply_markup=back_keyboard())
            return
        fam_order_id = record.get("fam_order_id")
        if not fam_order_id:
            await safe_edit_message_caption(query, "⚠️ FamPay order info nahi mila, Support se contact karein.", reply_markup=back_keyboard())
            return
        status = await process_fam_status(context, kind, rid, fam_order_id, "")
        amount_label = record["amount"] if kind == "topup" else record["price"]
        if status == "Success":
            msg = "✅ Payment confirm ho gaya! Wallet update ho gaya hai." if kind == "topup" else "✅ Payment confirm ho gaya! Admin ko notify kar diya, jaldi deliver hoga."
            await safe_edit_message_caption(query, msg, reply_markup=back_keyboard())
        elif status == "Failed":
            await safe_edit_message_caption(query, "❌ Payment fail ya expire ho gaya. Dobara try karein ya Support se contact karein.", reply_markup=back_keyboard())
        elif status == "error":
            await safe_edit_message_caption(query, "⚠️ Gateway status check nahi ho paya. Thodi der baad dobara try karein.", reply_markup=IKM([[InlineKeyboardButton("🔄 Check Status Now", callback_data=f"fam_check:{kind}:{rid}")], [InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")]]))
        else:
            cancel_cb = f"qrcancel_topup:{rid}" if kind == "topup" else f"qrcancel_order:{rid}"
            retry_buttons = IKM([
                [InlineKeyboardButton("🔄 Check Status Now", callback_data=f"fam_check:{kind}:{rid}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=cancel_cb)],
            ])
            await safe_edit_message_caption(query,
                f"⏳ *₹{amount_label:.0f}* ka payment abhi confirm nahi hua. Thodi der baad status dobara check karein. Payment ho chuka hai to dobara pay na karein.",
                parse_mode="Markdown", reply_markup=retry_buttons,
            )
        return

    if data.startswith("topupdone:"):
        tid = int(data.split(":")[1])
        topup = get_topup(tid)
        if not topup or topup["user_id"] != user.id:
            await safe_edit_message_caption(query, "Ye request nahi mili.", reply_markup=back_keyboard())
            return
        if topup["status"] != "pending":
            await safe_edit_message_caption(query, f"Ye request already {topup['status']} ho chuki hai.", reply_markup=back_keyboard())
            return
        set_topup_status(tid, "awaiting_admin")
        await safe_edit_message_caption(query, 
            f"⏳ *₹{topup['amount']:.0f}* ka payment verify ho raha hai — admin confirm karte hi wallet update ho jaayega.",
            parse_mode="Markdown", reply_markup=back_keyboard(),
        )
        admin_text = (
            f"💰 *New Top-up Request*\n"
            f"User: {escape_md(user.first_name)} (@{escape_md(user.username) or 'N/A'}, `{user.id}`)\n"
            f"Amount: ₹{topup['amount']:.0f}\n"
            f"Request ID: `{tid}`\n\n"
            f"Payment confirm hua? UPI app mein credit check karke approve/reject karein."
        )
        admin_buttons = IKM([
            [InlineKeyboardButton("✅ Approve", callback_data=f"topupapprove:{tid}", style="success"),
             InlineKeyboardButton("❌ Reject", callback_data=f"topupreject:{tid}", style="danger")],
        ])
        await notify_admins(context, admin_text, reply_markup=admin_buttons)
        return

    if data.startswith("topupapprove:") or data.startswith("topupreject:"):
        if not is_admin(user.id):
            await query.answer("⛔ Sirf admin approve/reject kar sakte hain.", show_alert=True)
            return
        approve = data.startswith("topupapprove:")
        tid = int(data.split(":")[1])
        topup = get_topup(tid)
        if not topup:
            await safe_edit_message_text(query, "Ye request nahi mili.")
            return
        if topup["status"] not in ("pending", "awaiting_admin"):
            await safe_edit_message_text(query, f"Ye request already *{topup['status']}* ho chuki hai (kisi aur admin ne process kar diya).", parse_mode="Markdown")
            return

        if approve:
            set_topup_status(tid, "approved")
            add_wallet_balance(topup["user_id"], topup["amount"])
            new_balance = get_or_create_user(topup["user_id"], None, None)["wallet_balance"]
            await safe_edit_message_text(query, f"✅ Approved — ₹{topup['amount']:.0f} wallet mein add ho gaya (Request `{tid}`).", parse_mode="Markdown")
            try:
                await safe_send_message(context.bot, 
                    chat_id=topup["user_id"],
                    text=f"🎉 ₹{topup['amount']:.2f} SUCCESSFULLY ADDED BALANCE IN YOUR WALLET BY ADMIN\n\nAny problem? Click Support button.",
                    parse_mode="Markdown", reply_markup=payment_success_keyboard(),
                )
            except Exception as e:
                logger.warning(f"Could not notify user {topup['user_id']} of approval: {e}")
        else:
            set_topup_status(tid, "rejected")
            await safe_edit_message_text(query, f"❌ Rejected — Request `{tid}`.", parse_mode="Markdown")
            try:
                await safe_send_message(context.bot, 
                    chat_id=topup["user_id"],
                    text=f"❌ Aapka ₹{topup['amount']:.0f} ka payment verify nahi ho paaya. Support se contact karein.",
                    reply_markup=back_keyboard(),
                )
            except Exception as e:
                logger.warning(f"Could not notify user {topup['user_id']} of rejection: {e}")
        return

    if data == "menu:orders" or data.startswith("orders_page:"):
        orders = get_successful_orders(user.id)
        if not orders:
            await safe_edit_message_text(query, "📝 *My Orders*\nAbhi tak koi successfully purchased key/order nahi hai.", parse_mode="Markdown", reply_markup=back_keyboard())
            return
        page = int(data.split(":")[1]) if data.startswith("orders_page:") else 0
        per_page = 8
        start_i = page * per_page
        page_orders = orders[start_i:start_i + per_page]
        rows = []
        for o in page_orders:
            pname = o.get("product_name") or "Product"
            rows.append([InlineKeyboardButton(f"#{o['id']}  {pname[:32]} — ₹{o['price']:.2f}", callback_data=f"orderdetail:{o['id']}")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅ Previous", callback_data=f"orders_page:{page-1}"))
        if start_i + per_page < len(orders):
            nav.append(InlineKeyboardButton("Next ➡", callback_data=f"orders_page:{page+1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="menu:main")])
        total_pages = (len(orders) + per_page - 1) // per_page
        desc_text, desc_entities = render_text_block("orders_text")
        desc_text = f"{desc_text}\n\n📦 Total successful orders: {len(orders)}\nPage {page+1}/{total_pages}"
        if desc_entities:
            desc_entities = [MessageEntity(type=e.type, offset=e.offset, length=e.length, url=e.url, language=e.language, custom_emoji_id=e.custom_emoji_id) for e in desc_entities]
            send_text, kwargs = send_kwargs_for((desc_text, desc_entities), reply_markup=IKM(rows))
        else:
            send_text, kwargs = send_kwargs_for((desc_text, None), reply_markup=IKM(rows))
        await safe_edit_message_text(query, send_text, **kwargs)
        return

    if data.startswith("orderdetail:"):
        oid = int(data.split(":")[1])
        o = get_order(oid)
        if not o or o.get("user_id") != user.id or o.get("status") != "delivered":
            await safe_edit_message_text(query, "Ye successful order nahi mila.", reply_markup=back_keyboard())
            return
        date_str = time.strftime("%d %b %Y, %I:%M %p", time.localtime(o.get("delivery_time") or o.get("created_at") or time.time()))
        key_text = o.get("delivered_key") or "Key record available nahi hai"
        text = (f"📦 *Order #{o['id']} — SUCCESSFUL*\n\n"
                f"🛍 Product: {escape_md(o.get('product_name') or 'Product')}\n"
                f"💰 Paid: ₹{o['price']:.2f}\n"
                f"📅 Date: {date_str}\n\n"
                f"🔑 *Key:*\n`{escape_md(key_text)}`")
        await safe_edit_message_text(query, text, parse_mode="Markdown", reply_markup=back_keyboard())
        return

    if data == "menu:mykeys":
        orders = get_successful_orders(user.id)
        # Show the fixed editable description directly on My Keys for anything
        # the user has not purchased. Purchased products are shown separately
        # as buttons and open only their own key history.
        desc_text, desc_entities = render_text_block("mykeys_notpurchased_text")
        purchased_count = sum(1 for o in orders if o.get("product_id") is not None)
        if purchased_count:
            prefix = "🔑 *MY KEYS*\n\n"
            if desc_text:
                prefix += desc_text + "\n\n"
            prefix += "Select a purchased product to view its keys:"
            if desc_entities:
                # The fixed description may contain Telegram entities; combining
                # it with a prefix would invalidate offsets, so send the profile
                # heading separately only when entity mode is active.
                # Preserve the description entities while shifting their offsets
                # below the short My Keys heading. No extra unpurchased button is shown.
                combined_text = prefix
                combined_entities = []
                # Keep description as plain text here only if entities are absent.
                # For entity mode, preserve the description exactly and use a short
                # unformatted heading above it.
                combined_text = "🔑 MY KEYS\n\n" + desc_text + "\n\nSelect a purchased product to view its keys:"
                shift = len("🔑 MY KEYS\n\n")
                combined_entities = [MessageEntity(type=e.type, offset=e.offset + shift, length=e.length, url=e.url, language=e.language, custom_emoji_id=e.custom_emoji_id) for e in desc_entities]
                send_text, kwargs = send_kwargs_for((combined_text, combined_entities), reply_markup=mykeys_keyboard(user.id))
                await safe_edit_message_text(query, send_text, **kwargs)
            else:
                await safe_edit_message_text(query, prefix, parse_mode="Markdown", reply_markup=mykeys_keyboard(user.id))
        else:
            send_text, kwargs = send_kwargs_for((desc_text, desc_entities), reply_markup=mykeys_keyboard(user.id))
            await safe_edit_message_text(query, send_text, **kwargs)
        return

    if data.startswith("mykeys:product:"):
        pid = int(data.split(":")[2])
        orders = [o for o in get_successful_orders(user.id) if o.get("product_id") == pid]
        if not orders:
            await safe_edit_message_text(query, "Ye product ki purchased history nahi mili.", reply_markup=mykeys_keyboard(user.id))
            return
        text, markup = purchased_product_text(user.id, pid)
        await safe_edit_message_text(query, text, parse_mode="Markdown", reply_markup=markup)
        return

    if data == "mykeys:notpurchased":
        text, entities = render_text_block("mykeys_notpurchased_text")
        send_text, kwargs = send_kwargs_for(
            (text, entities),
            reply_markup=IKM([[InlineKeyboardButton("⬅ Back to My Keys", callback_data="menu:mykeys")]])
        )
        await safe_edit_message_text(query, send_text, **kwargs)
        return

    if data == "menu:profilekeys" or data == "menu:profile":
        row = get_or_create_user(user.id, user.username, user.first_name)
        spent = get_user_spent(user.id)
        keys_no = get_user_keys_count(user.id)
        joined_ts = row.get("created_at") or int(time.time())
        joined_date = time.strftime("%d %b %Y", time.localtime(joined_ts))
        account_type = "Reseller" if is_premium(user.id) else "Regular User"
        # Keep username raw when saved Telegram entities are present; escaping it
        # here would make the backslash visible (e.g. @AMAM\_OFC) in entity mode.
        username_value = ("@" + user.username) if user.username else (row.get("first_name") or user.first_name or "User")
        profile_values = dict(user_id=row["user_id"], user_name=username_value,
                              balance=f"{row['wallet_balance']:.2f}", spent=f"{spent:.2f}", keys_no=keys_no,
                              joined_date=joined_date, account_type=account_type)
        text, entities = render_text_block("profile_text", **profile_values)
        # If an older saved profile description does not contain the new placeholders,
        # still show the two requested fields so existing databases get the update.
        saved_profile_text, _ = get_text_block("profile_text")
        missing_new_fields = "{joined_date}" not in saved_profile_text and "{account_type}" not in saved_profile_text
        if missing_new_fields:
            text = text.rstrip() + f"\n\n📅 *Joined:* {joined_date}\n🙋 *Account Type:* {account_type}"
        markup = profile_keyboard()
        profile_loading = await send_chat_loading(context.bot, query.message.chat_id)
        try:
            photos = await context.bot.get_user_profile_photos(user.id, limit=1)
            photo_file_id = photos.photos[0][0].file_id if photos.photos else None
        except Exception as e:
            logger.warning(f"Could not fetch profile photo for {user.id}: {e}")
            photo_file_id = None
        await delete_chat_loading(context.bot, profile_loading)
        if photo_file_id:
            cap_kwargs = {"caption": text, "reply_markup": markup}
            if entities is not None:
                cap_kwargs["caption_entities"] = entities
            else:
                cap_kwargs["parse_mode"] = "Markdown"
            try:
                await query.delete_message()
            except Exception:
                pass
            await safe_send_photo(context.bot, chat_id=query.message.chat_id, photo=photo_file_id, **cap_kwargs)
        else:
            send_text, kwargs = send_kwargs_for((text, entities), reply_markup=markup)
            await safe_edit_message_text(query, send_text, **kwargs)
        return

    if data == "menu:premium":
        price = float(get_setting("premium_price", "99"))
        days = int(get_setting("premium_days", "30"))
        until_str = ""
        if is_premium(user.id):
            until = get_premium_until(user.id)
            until_str = time.strftime("%d %b %Y", time.localtime(until))
            status = "Active Reseller"
        else:
            status = "Not a Reseller"
        text, kwargs = send_kwargs_for(
            render_text_block(
                "reseller_text",
                price=f"{price:.0f}",
                days=days,
                valid_until=until_str or "-",
                status=status,
            ),
            reply_markup=IKM([
                [InlineKeyboardButton(f"💳 Buy Premium — ₹{price:.0f}", callback_data="buy:premium", style="success")],
                [InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")],
            ]),
        )
        await safe_edit_message_text(query, text, **kwargs)
        return

    if data == "buy:premium":
        price = float(get_setting("premium_price", "99"))
        days = int(get_setting("premium_days", "30"))
        row = get_or_create_user(user.id, user.username, user.first_name)
        if row["wallet_balance"] < price:
            buttons = IKM([
                [InlineKeyboardButton("💰 Add Balance", callback_data="menu:balance")],
                [InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")],
            ])
            await safe_edit_message_text(query, 
                f"❌ Wallet balance kam hai (chahiye ₹{price:.0f}, hai ₹{row['wallet_balance']:.0f}).\nPehle Add Balance karein.",
                reply_markup=buttons,
            )
            return
        deduct_wallet_balance(user.id, price)
        new_until = extend_premium(user.id, days)
        until_str = time.strftime("%d %b %Y", time.localtime(new_until))
        await safe_edit_message_text(query, 
            f"🎉 *Premium activate ho gaya!*\nValid till: {until_str}\nAb sab purchases par discount milega.",
            parse_mode="Markdown", reply_markup=back_keyboard(),
        )
        return

    if data == "menu:referral":
        row = get_or_create_user(user.id, user.username, user.first_name)
        count = get_referral_count(user.id)
        bot_username = get_bot_username().lstrip("@")
        share_link = f"https://t.me/{bot_username}?start={row['referral_code']}"
        text, kwargs = send_kwargs_for(render_text_block("referral_text", referral_code=row["referral_code"], referred_count=count, share_link=share_link), reply_markup=back_keyboard())
        await safe_edit_message_text(query, text, **kwargs)
        return

    if data == "menu:share":
        bot_username = get_bot_username().lstrip("@")
        text, kwargs = send_kwargs_for(render_text_block("share_text", store_name=escape_md(STORE_NAME), bot_username=bot_username), reply_markup=back_keyboard())
        await safe_edit_message_text(query, text, **kwargs)
        return

    if data == "menu:wheel":
        buttons = [[InlineKeyboardButton("🎡 Spin Now", callback_data="spin:go")],
                   [InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")]]
        text, kwargs = send_kwargs_for(render_text_block("wheel_text"), reply_markup=IKM(buttons))
        await safe_edit_message_text(query, text, **kwargs)
        return

    if data == "spin:go":
        row = get_or_create_user(user.id, user.username, user.first_name)
        now = int(time.time())
        if now - row["last_spin"] < 86400:
            wait_hrs = round((86400 - (now - row["last_spin"])) / 3600, 1)
            await safe_edit_message_text(query, f"⏳ Aap already aaj spin kar chuke hain. {wait_hrs} ghante baad phir try karein.", reply_markup=back_keyboard())
            return

        # Motion/slot-style spin animation: keep the same Telegram message and
        # rapidly cycle through symbols before revealing the real prize.
        prize = random.choice(DAILY_WHEEL_PRIZES)
        set_last_spin(user.id, now)
        animation_symbols = ["💎", "🍒", "🍋", "🍊", "💰", "🎁", "⭐", "🍀"]
        animation_delays = [0.14, 0.14, 0.16, 0.17, 0.19, 0.22, 0.26, 0.31, 0.37, 0.44, 0.52]
        for i, delay in enumerate(animation_delays):
            symbol = animation_symbols[i % len(animation_symbols)]
            try:
                await query.edit_message_text(
                    f"🎡 *LUCKY SPIN*\n\n{symbol}  SPINNING...  {symbol}",
                    parse_mode="Markdown",
                )
                await asyncio.sleep(delay)
            except (BadRequest, NetworkError, TimedOut):
                # If Telegram rejects an intermediate edit, continue to the
                # final result instead of breaking the spin/payout flow.
                break

        if prize > 0:
            add_wallet_balance(user.id, prize)
            msg = f"🎉 *SPIN RESULT*\n\n💰 You won ₹{prize}!\n\nWallet mein reward add ho gaya."
        else:
            msg = "😢 *SPIN RESULT*\n\nIs baar kuch nahi mila.\nKal phir try karein!"
        await safe_edit_message_text(query, msg, parse_mode="Markdown", reply_markup=back_keyboard())
        return

    if data == "menu:howto":
        video_url = get_setting("howto_video_url", "")
        buttons = []
        if video_url:
            buttons.append([InlineKeyboardButton("HOW TO USE SETUP", url=video_url)])
        buttons.append([InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")])
        text, kwargs = send_kwargs_for(render_text_block("howto_text_block"), reply_markup=IKM(buttons))
        await safe_edit_message_text(query, text, **kwargs)
        return

    if data == "menu:support":
        text, entities = render_text_block("support_text")
        admin_username = get_setting("admin_contact_username", "")
        buttons = []
        if admin_username:
            buttons.append([InlineKeyboardButton("CONTACT ADMIN", url=f"https://t.me/{admin_username}")])
        buttons.append([InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")])
        send_text, kwargs = send_kwargs_for((text, entities), reply_markup=IKM(buttons))
        await safe_edit_message_text(query, send_text, **kwargs)
        return

    if data == "menu:updates":
        channel = get_setting("update_channel", "")
        buttons = []
        if channel:
            channel_url = channel if channel.startswith("http") else f"https://t.me/{channel.lstrip('@')}"
            buttons.append([InlineKeyboardButton("UPDATE CHANNEL", url=channel_url)])
        buttons.append([InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")])
        text, kwargs = send_kwargs_for(render_text_block("updates_text"), reply_markup=IKM(buttons))
        await safe_edit_message_text(query, text, **kwargs)
        return


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the current UTR-entry/input session without risking a paid transaction."""
    context.user_data.pop("awaiting_fam_utr", None)
    context.user_data.pop("topup_kpad_amount", None)
    context.user_data.pop("awaiting_topup_amount", None)
    await safe_reply_text(
        update.message,
        "✅ UTR entry / current input cancel kar diya. Payment request pending rahegi, kyunki /cancel se bank payment reverse nahi hota. Agar payment kar chuke hain to dobara pay na karein; Add Balance se wahi request/UTR verification continue karein. Nayi request shuru karne ke liye neeche Add Balance dabayein.",
        reply_markup=IKM([[InlineKeyboardButton("💰 Add Balance", callback_data="menu:balance")],
                          [InlineKeyboardButton("⬅ Back to Menu", callback_data="menu:main")]]),
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles free-text replies: wallet top-up amount, and all admin panel input flows."""
    user_id = update.effective_user.id
    text = update.message.text.strip()

    user = update.effective_user

    # ---- strict gateway UTR verification ----
    utr_request = context.user_data.get("awaiting_fam_utr")
    if utr_request:
        if text.lower() == "/cancel":
            context.user_data.pop("awaiting_fam_utr", None)
            await safe_reply_text(update.message, "UTR verification cancel kar diya. Payment request pending rahegi.", reply_markup=back_keyboard())
            return
        if not re.fullmatch(r"\d{12}", text):
            await safe_reply_text(update.message, "UTR exactly 12 digits ka hona chahiye. Dobara bhejein ya /cancel.")
            return
        context.user_data.pop("awaiting_fam_utr", None)
        kind, rid = utr_request["kind"], int(utr_request["record_id"])
        record = get_topup(rid) if kind == "topup" else get_order(rid)
        if not record or record["user_id"] != user_id or record["status"] != "pending" or not record.get("fam_order_id"):
            await safe_reply_text(update.message, "Pending payment nahi mili. Add Balance se dobara try karein.", reply_markup=back_keyboard())
            return
        result = await check_fam_order_status(record["fam_order_id"], text)
        if result.get("payment_status") == "Success":
            # Reuse the existing duplicate/amount-check and credit/delivery path, passing verified UTR.
            await _process_fam_verified_result(context, kind, rid, record, result)
            await safe_reply_text(update.message, "✅ Payment verify ho gaya." if kind == "topup" else "✅ Payment verify ho gaya; order processing continue hai.", reply_markup=payment_success_keyboard() if kind == "topup" else back_keyboard())
        else:
            await safe_reply_text(update.message, "⏳ Payment abhi verify nahi hua ya UTR match nahi hua. 10 seconds baad same payment ke liye UTR dobara submit karein. Payment pending hai; dobara pay na karein.", reply_markup=IKM([[InlineKeyboardButton("🔄 Submit UTR Again", callback_data=f"fam_utr:{kind}:{rid}")]]))
        return

    # ---- coupon code entry ----
    plan_id = context.user_data.get("awaiting_coupon_for_plan")
    if plan_id:
        context.user_data["awaiting_coupon_for_plan"] = None
        plan = get_plan(plan_id)
        if not plan:
            await safe_reply_text(update.message, "Ye plan available nahi hai.", reply_markup=back_keyboard())
            return
        product = get_product(plan["product_id"])
        coupon = get_coupon(text)
        valid, err = coupon_is_valid(coupon)
        if not valid:
            await safe_reply_text(update.message, err, reply_markup=IKM([[InlineKeyboardButton("⬅ Back", callback_data=f"shop_plan:{plan_id}")]]))
            return
        context.user_data.setdefault("applied_coupons", {})[str(plan_id)] = coupon["code"]
        pl_text, markup = build_plan_payment_screen(user, plan, product, context)
        await safe_reply_text(update.message, f"✅ Coupon `{coupon['code']}` apply ho gaya!\n\n{pl_text}", parse_mode="Markdown", reply_markup=markup)
        return

    # ---- wallet top-up ----
    if context.user_data.get("awaiting_topup"):
        context.user_data["awaiting_topup"] = False
        try:
            amount = float(text)
        except ValueError:
            await safe_reply_text(update.message, "Sahi number bhejein (jaise 150).")
            return
        await start_wallet_topup(update, context, user_id, amount, via_query=False)
        return

    # ---- admin panel flows ----
    admin_state = context.user_data.get("admin_state")
    if not admin_state:
        return
    if not is_admin(user_id):
        context.user_data["admin_state"] = None
        return

    if admin_state == "setadityaapikey":
        context.user_data["admin_state"] = None
        clean = text.strip()
        if clean.lower() in ("off", "remove", "none", "-"):
            set_setting("aditya_api_key", "")
            await safe_reply_text(update.message, "✅ Aditya API Key remove ho gayi.", reply_markup=admin_back_keyboard())
            return
        set_setting("aditya_api_key", clean)
        masked = (clean[:4] + "..." + clean[-4:]) if len(clean) > 8 else clean
        await safe_reply_text(update.message, f"✅ Aditya API Key save ho gayi: `{masked}`", parse_mode="Markdown", reply_markup=admin_back_keyboard())
        return

    if admin_state == "addproduct_aditya_details":
        context.user_data["admin_state"] = None
        temp = context.user_data.get("admin_temp") or {}
        parts = [seg[2].strip() for seg in split_with_offsets(text, "|")]
        existing_pid = temp.get("existing_product_id")
        existing_plan_id = temp.get("existing_plan_id")
        remote_name = temp.get("remote_name") or "Aditya Product"
        api_cost = float(temp.get("api_price"))
        try:
            if existing_pid:
                if len(parts) != 1:
                    await safe_reply_text(update.message, "Existing product ke liye sirf Selling Price bhejein. Example: `70`", parse_mode="Markdown", reply_markup=admin_back_keyboard())
                    return
                selling_price = float(parts[0])
                if selling_price < 0:
                    raise ValueError
                if existing_plan_id:
                    update_aditya_plan(existing_plan_id, selling_price, temp.get("duration_value"), temp.get("duration_unit"), api_cost)
                    plan_id = existing_plan_id
                    action_text = "updated"
                else:
                    plan_id = add_aditya_plan(existing_pid, f"{temp.get('duration_value')} {temp.get('duration_unit')}", selling_price, temp.get("duration_id"), temp.get("duration_value"), temp.get("duration_unit"), api_cost)
                    action_text = "added"
                await safe_reply_text(update.message,
                    f"✅ *Aditya Product Updated*\n\nProduct: *{escape_md(remote_name)}*\nDuration: `{temp.get('duration_value')} {temp.get('duration_unit')}`\nAditya Cost: `₹{api_cost:.2f}`\nSelling Price: `₹{selling_price:.2f}`\nPlan ID: `{plan_id}`\n\nExisting product mein duration {action_text} ho gaya.",
                    parse_mode="Markdown", reply_markup=product_quick_config_keyboard(existing_pid))
                return

            if len(parts) < 3:
                await safe_reply_text(update.message, "Format galat hai. Use: `Selling Price | Description | Category`\nExample: `70 | My description | Android`", parse_mode="Markdown", reply_markup=admin_back_keyboard())
                return
            selling_price = float(parts[0])
            if selling_price < 0:
                raise ValueError
            description, category = parts[1], parts[2]
        except ValueError:
            await safe_reply_text(update.message, "Selling Price valid number hona chahiye. Example: `70`", parse_mode="Markdown", reply_markup=admin_back_keyboard())
            return

        pid = add_product(remote_name, description, category, reseller_source="aditya", api_product_id=temp.get("api_product_id"), api_product_slug=temp.get("api_product_slug"))
        plan_label = f"{temp.get('duration_value')} {temp.get('duration_unit')}".strip()
        plan_id = add_aditya_plan(pid, plan_label, selling_price, temp.get("duration_id"), temp.get("duration_value"), temp.get("duration_unit"), api_cost)
        await safe_reply_text(update.message,
            f"✅ *Aditya Product Added*\n\nProduct ID: `{temp.get('api_product_id')}`\nProduct: *{escape_md(remote_name)}*\nSlug: `{temp.get('api_product_slug') or '-'}`\nDuration ID: `{temp.get('duration_id')}`\nDuration: `{temp.get('duration_value')} {temp.get('duration_unit')}`\nAditya Cost: `₹{api_cost:.2f}`\nSelling Price: `₹{selling_price:.2f}`\nLocal Plan ID: `{plan_id}`\n\nAb product ko configure karein:",
            parse_mode="Markdown", reply_markup=product_quick_config_keyboard(pid))
        return

    if admin_state == "addproduct":
        context.user_data["admin_state"] = None
        segments = split_with_offsets(text, "|")
        parts = [seg[2].strip() for seg in segments]
        if len(parts) < 3:
            await safe_reply_text(update.message, 
                "Format galat hai. Use:\n`Name | Description | Category`",
                parse_mode="Markdown", reply_markup=admin_back_keyboard(),
            )
            return
        name, description, category = parts[0], parts[1], parts[2]
        if not name:
            await safe_reply_text(update.message, "Product ka naam khali nahi ho sakta.", reply_markup=admin_back_keyboard())
            return

        desc_entities_json = ""
        emoji_count = 0
        if len(segments) > 1 and update.message.entities:
            raw_start, raw_end, raw_desc = segments[1]
            leading_ws = utf16_len(raw_desc) - utf16_len(raw_desc.lstrip())
            sub_start = raw_start + leading_ws
            sub_end = sub_start + utf16_len(description)
            message_entities = extract_message_entities(update.message.entities, sub_start, sub_end)
            emoji_count = sum(1 for e in message_entities if e.get("type") == "custom_emoji")
            desc_entities_json = entities_to_json(message_entities)

        source = (context.user_data.get("admin_temp") or {}).get("reseller_source", "banti")
        pid = add_product(name, description, category, desc_entities_json, reseller_source=source)
        emoji_note = f"\n({emoji_count} custom emoji description mein preserve ho gaye.)" if emoji_count else ""
        await safe_reply_text(update.message, 
            f"✅ Product added (ID: {pid}): {name}{emoji_note}\n\nAb isse configure karein:",
            reply_markup=product_quick_config_keyboard(pid),
        )
        return

    if admin_state == "addplan":
        context.user_data["admin_state"] = None
        pid = context.user_data.get("admin_temp", {}).get("product_id")
        product = get_product(pid) if pid else None
        if not product:
            await safe_reply_text(update.message, "Product nahi mila, dobara try karein.", reply_markup=admin_back_keyboard())
            return
        plan_pairs = []
        for chunk in text.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if ":" not in chunk:
                await safe_reply_text(update.message, f"Format galat hai: `{chunk}`. Use: Label:Price (jaise `1 day:80`)", parse_mode="Markdown", reply_markup=admin_back_keyboard())
                return
            label, price_str = chunk.rsplit(":", 1)
            label = label.strip()
            try:
                price = float(price_str.strip())
            except ValueError:
                await safe_reply_text(update.message, f"Price ek number hona chahiye: `{chunk}`", parse_mode="Markdown", reply_markup=admin_back_keyboard())
                return
            plan_pairs.append((label, price))
        if not plan_pairs:
            await safe_reply_text(update.message, "Kam se kam 1 duration:price dena zaroori hai.", reply_markup=admin_back_keyboard())
            return
        for label, price in plan_pairs:
            add_plan(pid, label, price)
        plans_summary = ", ".join(f"{label} ₹{price:.0f}" for label, price in plan_pairs)
        buttons = IKM([
            [InlineKeyboardButton("➕ Aur Plan Add Karein", callback_data=f"addplanprod:{pid}")],
            [InlineKeyboardButton("🔌 Set Delivery Mode (API/Manual)", callback_data=f"admdel:{pid}")],
            [InlineKeyboardButton("🔢 Set Product PID", callback_data=f"admsetpid:{pid}")],
            [InlineKeyboardButton("⏱️ Set Plan API Duration", callback_data=f"admapidur_prod:{pid}")],
            [InlineKeyboardButton("🎨 Set Product Icon", callback_data=f"admicon:{pid}")],
            [InlineKeyboardButton("🖼️ Set Product Photo", callback_data=f"admphoto:{pid}")],
            [InlineKeyboardButton("📢 Set Product Channel", callback_data=f"admchannel:{pid}")],
            [InlineKeyboardButton("🛠️ Product Maintenance", callback_data=f"admmaintenance:{pid}"),],
            [InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")],
        ])
        await safe_reply_text(update.message, f"✅ *{product['name']}* mein add ho gaya:\n{plans_summary}", parse_mode="Markdown", reply_markup=buttons)
        return

    if admin_state == "editplanprice":
        context.user_data["admin_state"] = None
        plan_id = context.user_data.get("admin_temp", {}).get("plan_id")
        try:
            new_price = float(text)
        except ValueError:
            await safe_reply_text(update.message, "Price ek number hona chahiye.", reply_markup=admin_back_keyboard())
            return
        ok = update_plan_price(plan_id, new_price)
        await safe_reply_text(update.message, f"✅ Price update ho gaya: ₹{new_price}" if ok else "Plan nahi mila.", reply_markup=admin_back_keyboard())
        return

    if admin_state == "editdesc":
        context.user_data["admin_state"] = None
        pid = context.user_data.get("admin_temp", {}).get("product_id")
        # Here the whole message IS the description, so entity offsets need no adjustment.
        message_entities = extract_message_entities(update.message.entities)
        entities_json = entities_to_json(message_entities)
        ok = set_product_description(pid, text, entities_json)
        if not ok:
            await safe_reply_text(update.message, "Product nahi mila.", reply_markup=admin_back_keyboard())
        else:
            custom_count = sum(1 for e in message_entities if e.get("type") == "custom_emoji")
            note = f" {custom_count} custom emoji bhi preserve ho gaye." if custom_count else ""
            await safe_reply_text(update.message,
                f"✅ Description update ho gaya — Telegram formatting exactly preserve hogi.{note}",
                reply_markup=admin_back_keyboard(),
            )
        return

    if admin_state == "set_sellingproofs_link":
        context.user_data["admin_state"] = None
        clean = text.strip()
        if clean.lower() in ("off", "remove", "none", "-"):
            set_setting("selling_proofs_link", "")
            await safe_reply_text(update.message, "✅ Selling Proofs channel link remove ho gaya.", reply_markup=admin_back_keyboard())
            return
        if clean.startswith("@"):
            clean = f"https://t.me/{clean[1:]}"
        elif clean.startswith("t.me/"):
            clean = "https://" + clean
        if not (clean.startswith("https://t.me/") or clean.startswith("http://t.me/")):
            await safe_reply_text(update.message, "Sahi Telegram channel link bhejein, jaise `https://t.me/YourChannel`.", parse_mode="Markdown", reply_markup=admin_back_keyboard())
            context.user_data["admin_state"] = "set_sellingproofs_link"
            return
        set_setting("selling_proofs_link", clean)
        set_setting("selling_proofs_enabled", "1")
        await safe_reply_text(update.message, f"✅ Selling Proofs channel link save ho gaya: {clean}", reply_markup=admin_back_keyboard())
        return

    if admin_state == "setproofchannel":
        context.user_data["admin_state"] = None
        clean = text.strip()
        target = normalize_channel_target(clean)
        if clean.lower() in ("off", "remove", "none", "-"):
            set_setting("purchase_proof_channel", "")
            await safe_reply_text(update.message, "✅ Purchase Proof Channel band kar diya gaya.", reply_markup=admin_back_keyboard())
            return
        if not (target.startswith("@") or target.startswith("-100") or target.lstrip("-").isdigit()):
            await safe_reply_text(update.message,
                "Sahi public Telegram channel username/link ya numeric chat ID bhejein.\nExample: @YourChannel or https://t.me/YourChannel",
                reply_markup=admin_back_keyboard())
            context.user_data["admin_state"] = "setproofchannel"
            return
        set_setting("purchase_proof_channel", target)
        await safe_reply_text(update.message,
            f"✅ Purchase Proof Channel save ho gaya: {target}\n\nAb successful key delivery ke baad automatic proof post hoga. Bot ko channel ka admin/post permission dena zaroori hai.",
            reply_markup=admin_back_keyboard())
        return

    if admin_state == "setchannel":
        context.user_data["admin_state"] = None
        set_setting("update_channel", text)
        await safe_reply_text(update.message, f"✅ Update channel update ho gaya: {text}", reply_markup=admin_back_keyboard())
        return

    if admin_state == "setupi":
        context.user_data["admin_state"] = None
        set_setting("upi_id", text)
        await safe_reply_text(update.message, f"✅ UPI ID update ho gaya: `{text}`", parse_mode="Markdown", reply_markup=admin_back_keyboard())
        return

    if admin_state == "setresellerkey":
        context.user_data["admin_state"] = None
        set_setting("reseller_api_key", text)
        masked = (text[:4] + "..." + text[-4:]) if len(text) > 8 else text
        await safe_reply_text(update.message, f"✅ Reseller API Key update ho gayi: `{masked}`", parse_mode="Markdown", reply_markup=admin_back_keyboard())
        return

    if admin_state == "setresellermasterkey":
        context.user_data["admin_state"] = None
        clean = text.strip()
        if clean.lower() in ("off", "remove", "none", "-"):
            set_setting("reseller_master_key", "")
            await safe_reply_text(update.message, "✅ Master Key hata di gayi — ab x-master-key header nahi bhejega.", reply_markup=admin_back_keyboard())
            return
        set_setting("reseller_master_key", clean)
        masked = (clean[:4] + "..." + clean[-4:]) if len(clean) > 8 else clean
        await safe_reply_text(update.message, f"✅ Reseller Master Key update ho gayi: `{masked}`", parse_mode="Markdown", reply_markup=admin_back_keyboard())
        return

    if admin_state == "setzapupikey":
        context.user_data["admin_state"] = None
        clean = text.strip()
        if clean.lower() in ("off", "remove", "none", "-"):
            set_setting("zapupi_api_key", "")
            await safe_reply_text(update.message, "✅ ZapUPI key hata di gayi — ab wapas manual UPI QR + admin-approve flow use hoga.", reply_markup=admin_back_keyboard())
            return
        set_setting("zapupi_api_key", clean)
        masked = (clean[:4] + "..." + clean[-4:]) if len(clean) > 8 else clean
        await safe_reply_text(update.message, 
            f"✅ ZapUPI Key update ho gayi: `{masked}`\n\nAb 'Add Balance' try karke test kar sakte hain — payment ke baad wallet automatic credit hona chahiye.",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if admin_state == "setfamkey":
        context.user_data["admin_state"] = None
        clean = text.strip()
        if clean.lower() in ("off", "remove", "none", "-"):
            set_setting("fam_api_key", "")
            await safe_reply_text(update.message, "✅ FamPay Gateway key hata di gayi.", reply_markup=admin_back_keyboard())
            return
        set_setting("fam_api_key", clean)
        masked = (clean[:4] + "..." + clean[-4:]) if len(clean) > 8 else clean
        await safe_reply_text(update.message, 
            f"✅ FAM API Key update ho gayi: `{masked}`\n\nAb payment test kar sakte hain — QR/payment link create hoga aur payment automatic verify hoga.",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if admin_state == "setproductchannel":
        context.user_data["admin_state"] = None
        pid = context.user_data.get("admin_temp", {}).get("product_id")
        product = get_product(pid) if pid else None
        if not product:
            await safe_reply_text(update.message, "Product nahi mila.", reply_markup=admin_back_keyboard())
            return
        clean = text.strip()
        if clean and not clean.lower() in ("off", "remove", "none", "-") and not (clean.startswith("http://") or clean.startswith("https://") or clean.startswith("t.me/") or clean.startswith("@")):
            await safe_reply_text(update.message, "Sahi Telegram channel link bhejein, jaise `https://t.me/YourChannel`.", parse_mode="Markdown", reply_markup=product_quick_config_keyboard(pid))
            context.user_data["admin_state"] = "setproductchannel"
            return
        set_product_channel(pid, clean)
        saved = get_product(pid).get("channel_link") or "OFF"
        await safe_reply_text(update.message, f"✅ *{escape_md(product['name'])}* ka product channel save ho gaya:\n{saved}", parse_mode="Markdown", reply_markup=product_quick_config_keyboard(pid))
        return

    if admin_state == "sethowto":
        context.user_data["admin_state"] = None
        set_setting("howto_text", text)
        entities_json = entities_to_json(extract_message_entities(update.message.entities))
        set_setting("howto_text_entities", entities_json)
        await safe_reply_text(update.message, f"✅ How To text update ho gaya:\n{text}", reply_markup=admin_back_keyboard())
        return

    if admin_state == "sethowtovideo":
        context.user_data["admin_state"] = None
        set_setting("howto_video_url", text.strip())
        await safe_reply_text(update.message, f"✅ How To video link update ho gaya:\n{text.strip()}", reply_markup=admin_back_keyboard())
        return

    if admin_state == "setbotusername":
        context.user_data["admin_state"] = None
        clean = text.strip().lstrip("@")
        set_setting("bot_username", clean)
        await safe_reply_text(update.message, f"✅ Bot username update ho gaya: @{clean}", reply_markup=admin_back_keyboard())
        return

    if admin_state == "setadmincontact":
        context.user_data["admin_state"] = None
        clean = text.strip().lstrip("@")
        set_setting("admin_contact_username", clean)
        await safe_reply_text(update.message, f"✅ Admin contact update ho gaya: @{clean}", reply_markup=admin_back_keyboard())
        return

    if admin_state == "setpremium":
        context.user_data["admin_state"] = None
        parts = [p.strip() for p in text.split("|")]
        if len(parts) != 2:
            await safe_reply_text(update.message, "Format galat hai. Use: `Price | Days`", parse_mode="Markdown", reply_markup=admin_back_keyboard())
            return
        try:
            price = float(parts[0])
            days = int(parts[1])
        except ValueError:
            await safe_reply_text(update.message, "Price/Days numbers hone chahiye.", reply_markup=admin_back_keyboard())
            return
        set_setting("premium_price", str(price))
        set_setting("premium_days", str(days))
        await safe_reply_text(update.message, f"✅ Reseller settings update ho gayi: ₹{price:.0f} / {days} din", reply_markup=admin_back_keyboard())
        return

    if admin_state == "createcoupon":
        context.user_data["admin_state"] = None
        parts = [p.strip() for p in text.split("|")]
        if len(parts) != 4:
            await safe_reply_text(update.message, 
                "Format galat hai. Use:\n`CODE | percent/fixed | value | maxuses`\nExample: `SAVE20 | percent | 20 | 0`",
                parse_mode="Markdown", reply_markup=admin_back_keyboard(),
            )
            return
        code, dtype, value_str, maxuses_str = parts
        code = code.strip().upper()
        dtype = dtype.strip().lower()
        if not code or " " in code:
            await safe_reply_text(update.message, "Coupon code khali ya space wala nahi ho sakta.", reply_markup=admin_back_keyboard())
            return
        if dtype not in ("percent", "fixed"):
            await safe_reply_text(update.message, "Type sirf `percent` ya `fixed` ho sakta hai.", parse_mode="Markdown", reply_markup=admin_back_keyboard())
            return
        try:
            value = float(value_str)
            maxuses = int(maxuses_str)
        except ValueError:
            await safe_reply_text(update.message, "Value aur maxuses numbers hone chahiye.", reply_markup=admin_back_keyboard())
            return
        if value <= 0:
            await safe_reply_text(update.message, "Discount value 0 se zyada honi chahiye.", reply_markup=admin_back_keyboard())
            return
        create_coupon(code, dtype, value, max_uses=(None if maxuses <= 0 else maxuses))
        val_display = f"{value:.0f}%" if dtype == "percent" else f"₹{value:.0f}"
        limit_display = "unlimited" if maxuses <= 0 else str(maxuses)
        await safe_reply_text(update.message, 
            f"✅ Coupon `{code}` ban gaya!\nDiscount: {val_display}\nMax uses: {limit_display}",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    async def resolve_reseller_target(raw_text):
        """Resolve a reseller target from numeric Telegram ID or @username.
        For usernames, prefer a user already stored in our DB; otherwise ask Telegram.
        Returns (user_id, username, first_name) or (None, None, None).
        """
        value = (raw_text or "").strip()
        if not value:
            return None, None, None

        if value.lstrip("-").isdigit():
            target_id = int(value)
            conn = get_conn()
            row = conn.execute("SELECT username, first_name FROM users WHERE user_id = ?", (target_id,)).fetchone()
            conn.close()
            if row:
                return target_id, row["username"], row["first_name"]
            try:
                chat = await context.bot.get_chat(target_id)
                return target_id, getattr(chat, "username", None), getattr(chat, "first_name", None) or "User"
            except Exception:
                return target_id, None, "User"

        username = value.lstrip("@").strip()
        if not username or any(ch.isspace() for ch in username):
            return None, None, None

        conn = get_conn()
        row = conn.execute(
            "SELECT user_id, username, first_name FROM users WHERE lower(username) = lower(?)",
            (username,),
        ).fetchone()
        conn.close()
        if row:
            return int(row["user_id"]), row["username"], row["first_name"]

        try:
            chat = await context.bot.get_chat("@" + username)
            target_id = int(chat.id)
            return target_id, getattr(chat, "username", None) or username, getattr(chat, "first_name", None) or "User"
        except Exception:
            return None, None, None

    if admin_state == "addreseller":
        context.user_data["admin_state"] = None
        target_id, target_username, target_first_name = await resolve_reseller_target(text)
        if target_id is None:
            await safe_reply_text(
                update.message,
                "❌ User nahi mila. Sahi Telegram numeric User ID ya @username bhejein. Username wale user ne bot ko start kiya hona chahiye.",
                reply_markup=admin_back_keyboard(),
            )
            return

        # Ensure the target exists in the bot DB, then grant the same Premium/Reseller
        # membership used by the existing reseller pricing system.
        get_or_create_user(target_id, target_username, target_first_name)
        days = max(1, int(get_setting("premium_days", "30") or 30))
        was_active = is_premium(target_id)
        new_until = extend_premium(target_id, days)
        until_text = time.strftime("%d-%m-%Y %H:%M", time.localtime(new_until))
        label = f"@{target_username}" if target_username else str(target_id)
        status = "Reseller status extend ho gaya" if was_active else "Reseller status active ho gaya"
        await safe_reply_text(
            update.message,
            f"✅ {label} ab Reseller hai.\n\n"
            f"👑 {status}\n"
            f"⏳ Valid Till: {until_text}\n\n"
            "Is user ko wahi existing admin-fixed **Reseller Price** dikhengi jo bot me plans ke liye set hain.",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if admin_state == "removereseller":
        context.user_data["admin_state"] = None
        target_id, target_username, target_first_name = await resolve_reseller_target(text)
        if target_id is None:
            await safe_reply_text(
                update.message,
                "❌ User nahi mila. Sahi Telegram numeric User ID ya @username bhejein. Username wale user ne bot ko start kiya hona chahiye.",
                reply_markup=admin_back_keyboard(),
            )
            return
        was_premium = is_premium(target_id)
        revoke_premium(target_id)
        label = f"@{target_username}" if target_username else str(target_id)
        msg = f"✅ {label} ka Reseller status hata diya gaya." if was_premium else f"ℹ️ {label} ka Reseller status pehle se active nahi tha — phir bhi clear kar diya."
        await safe_reply_text(update.message, msg, reply_markup=admin_back_keyboard())
        return

    if admin_state == "addbalanceuser":
        context.user_data["admin_state"] = None

        # Format: +20 123456789 / -20 @username
        parts = text.split()
        if len(parts) != 2 or not re.fullmatch(r"[+-]\s*\d+(?:\.\d{1,2})?", parts[0]):
            await safe_reply_text(
                update.message,
                "❌ Format galat. Use:\n`+20 123456789`\n`-20 123456789`\n\nYa username ke saath:\n`+20 @username` / `-20 @username`",
                parse_mode="Markdown", reply_markup=admin_back_keyboard(),
            )
            return

        try:
            delta = float(parts[0])
        except ValueError:
            await safe_reply_text(update.message, "❌ Amount sahi number hona chahiye.", reply_markup=admin_back_keyboard())
            return
        if delta == 0:
            await safe_reply_text(update.message, "❌ Amount 0 nahi ho sakta.", reply_markup=admin_back_keyboard())
            return

        target_raw = parts[1].strip()
        target_id = None
        target_username = None
        target_first_name = "User"

        if target_raw.lstrip("-").isdigit():
            target_id = int(target_raw)
            conn = get_conn()
            row = conn.execute("SELECT username, first_name FROM users WHERE user_id = ?", (target_id,)).fetchone()
            conn.close()
            if row:
                target_username = row["username"]
                target_first_name = row["first_name"] or "User"
            else:
                try:
                    chat = await context.bot.get_chat(target_id)
                    target_username = getattr(chat, "username", None)
                    target_first_name = getattr(chat, "first_name", None) or "User"
                except Exception:
                    # A numeric ID is already enough to adjust an existing/new DB user.
                    pass
        else:
            username = target_raw.lstrip("@").strip()
            if not username or any(ch.isspace() for ch in username):
                await safe_reply_text(update.message, "❌ Sahi User ID ya @username bhejein.", reply_markup=admin_back_keyboard())
                return
            conn = get_conn()
            row = conn.execute(
                "SELECT user_id, username, first_name FROM users WHERE lower(username) = lower(?)",
                (username,),
            ).fetchone()
            conn.close()
            if row:
                target_id = int(row["user_id"])
                target_username = row["username"]
                target_first_name = row["first_name"] or "User"
            else:
                try:
                    chat = await context.bot.get_chat("@" + username)
                    target_id = int(chat.id)
                    target_username = getattr(chat, "username", None) or username
                    target_first_name = getattr(chat, "first_name", None) or "User"
                except Exception:
                    await safe_reply_text(update.message, "❌ User nahi mila. Sahi numeric User ID ya @username bhejein.", reply_markup=admin_back_keyboard())
                    return

        get_or_create_user(target_id, target_username, target_first_name)
        before = float(get_or_create_user(target_id, target_username, target_first_name)["wallet_balance"] or 0)
        if delta > 0:
            add_wallet_balance(target_id, delta)
        else:
            # Admin correction: never let a wallet go below ₹0.
            amount_to_remove = abs(delta)
            if before < amount_to_remove:
                await safe_reply_text(
                    update.message,
                    f"❌ Balance ₹{before:.2f} hai, isliye ₹{amount_to_remove:.2f} minus nahi kiya gaya. Balance negative nahi hone diya.",
                    reply_markup=admin_back_keyboard(),
                )
                return
            deduct_wallet_balance(target_id, amount_to_remove)

        after = float(get_or_create_user(target_id, target_username, target_first_name)["wallet_balance"] or 0)
        label = f"@{target_username}" if target_username else str(target_id)
        action = "add" if delta > 0 else "minus"
        await safe_reply_text(
            update.message,
            f"✅ {label} ka balance {action} ho gaya.\n\n"
            f"User ID: `{target_id}`\n"
            f"Change: {'+' if delta > 0 else ''}₹{delta:.2f}\n"
            f"New Balance: ₹{after:.2f}",
            parse_mode="Markdown", reply_markup=admin_back_keyboard(),
        )
        return

    if admin_state == "addadmin":
        context.user_data["admin_state"] = None
        try:
            new_admin_id = int(text)
        except ValueError:
            await safe_reply_text(update.message, "Numeric Telegram user ID bhejein.", reply_markup=admin_back_keyboard())
            return
        add_admin_db(new_admin_id, added_by=user_id)
        await safe_reply_text(update.message, f"✅ User {new_admin_id} ab admin hai.", reply_markup=admin_back_keyboard())
        return

    if admin_state == "setdelivery_apicode":
        context.user_data["admin_state"] = None
        pid = context.user_data.get("admin_temp", {}).get("product_id")
        set_product_delivery(pid, "api", text)
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Haan, chahiye", callback_data=f"setandroid:1:{pid}"),
             InlineKeyboardButton("❌ Nahi", callback_data=f"setandroid:0:{pid}")],
        ])
        await safe_reply_text(
            update.message,
            f"✅ Ye product ab *API* delivery par set hai (code: `{text}`).\n\n"
            f"Kya is product ke API call mein customer ka *Android ID* bhejna zaroori hai? (jaise BALA MOD XYZ V1 ke liye)",
            parse_mode="Markdown", reply_markup=buttons,
        )
        return

    if admin_state == "setpid":
        context.user_data["admin_state"] = None
        pid = context.user_data.get("admin_temp", {}).get("product_id")
        pid_value = text.strip()
        set_product_delivery(pid, "api", pid_value)
        await safe_reply_text(
            update.message,
            f"✅ PID `{pid_value}` set ho gaya aur ye product **API (auto-deliver)** mode par hai.\n"
            f"Customer buy karega to bot khud reseller se key nikaal ke turant de dega.\n\nAage aur configure karna hai?",
            parse_mode="Markdown", reply_markup=product_quick_config_keyboard(pid),
        )
        return

    if admin_state == "setapiduration":
        context.user_data["admin_state"] = None
        temp = context.user_data.get("admin_temp", {})
        plid = temp.get("plan_id")
        pid = temp.get("product_id")
        value = text.strip()
        set_plan_api_duration(plid, value)
        plan = get_plan(plid)
        label = plan["label"] if plan else f"#{plid}"
        if pid is None and plan:
            pid = plan["product_id"]
        await safe_reply_text(update.message, 
            f"✅ Plan *{escape_md(label)}* ke liye reseller API duration set ho gaya: `{value}`\n\nAgle plan ka duration set karna hai?",
            parse_mode="Markdown", reply_markup=api_duration_plan_list_keyboard(pid) if pid else admin_back_keyboard(),
        )
        return

    if admin_state == "setresellerprice":
        context.user_data["admin_state"] = None
        temp = context.user_data.get("admin_temp", {})
        plid = temp.get("plan_id")
        pid = temp.get("product_id")
        value = text.strip()
        if value.lower() in ("off", "remove", "none", "-"):
            set_plan_reseller_price(plid, None)
            msg = "✅ Reseller price hata di gayi — ab normal price hi dikhega."
        else:
            try:
                price = float(value)
            except ValueError:
                await safe_reply_text(update.message, "Number bhejein (jaise 70), ya reset ke liye 'off'.")
                return
            set_plan_reseller_price(plid, price)
            msg = f"✅ Reseller price set ho gayi: ₹{price:.0f}"
        plan = get_plan(plid)
        label = plan["label"] if plan else f"#{plid}"
        if pid is None and plan:
            pid = plan["product_id"]
        await safe_reply_text(update.message, 
            f"{msg} (Plan: *{escape_md(label)}*)\n\nAgle plan ka reseller price set karna hai?",
            parse_mode="Markdown", reply_markup=reseller_price_plan_list_keyboard(pid) if pid else admin_back_keyboard(),
        )
        return

    if admin_state == "seticon":
        context.user_data["admin_state"] = None
        pid = context.user_data.get("admin_temp", {}).get("product_id")

        custom_emoji_id = None
        if update.message.entities:
            for entity in update.message.entities:
                if entity.type == "custom_emoji" and getattr(entity, "custom_emoji_id", None):
                    custom_emoji_id = entity.custom_emoji_id
                    break

        if custom_emoji_id:
            set_product_icon(pid, icon_emoji=None, icon_custom_emoji_id=custom_emoji_id)
            await safe_reply_text(update.message, 
                "✅ Custom emoji icon set ho gaya.\n"
                "⚠️ Ye tabhi dikhega jab bot banane wale account par Telegram Premium active ho — nahi to Telegram ye icon nahi dikhayega.\n\nAage aur configure karna hai?",
                reply_markup=product_quick_config_keyboard(pid),
            )
        else:
            set_product_icon(pid, icon_emoji=text.strip(), icon_custom_emoji_id=None)
            await safe_reply_text(update.message, f"✅ Icon set ho gaya: {text.strip()}\n\nAage aur configure karna hai?", reply_markup=product_quick_config_keyboard(pid))
        return

    if admin_state == "setcategoryicon":
        context.user_data["admin_state"] = None
        cat = context.user_data.get("admin_temp", {}).get("category")

        custom_emoji_id = None
        if update.message.entities:
            for entity in update.message.entities:
                if entity.type == "custom_emoji" and getattr(entity, "custom_emoji_id", None):
                    custom_emoji_id = entity.custom_emoji_id
                    break

        if custom_emoji_id:
            set_category_icon(cat, icon_emoji=None, icon_custom_emoji_id=custom_emoji_id)
            await safe_reply_text(update.message, 
                f"✅ *{escape_md(cat)}* ka custom emoji icon set ho gaya.\n"
                "⚠️ Ye tabhi dikhega jab bot banane wale account par Telegram Premium active ho.",
                parse_mode="Markdown", reply_markup=admin_back_keyboard(),
            )
        else:
            set_category_icon(cat, icon_emoji=text.strip(), icon_custom_emoji_id=None)
            await safe_reply_text(update.message, f"✅ *{escape_md(cat)}* ka icon set ho gaya: {text.strip()}", parse_mode="Markdown", reply_markup=admin_back_keyboard())
        return

    if admin_state == "setbackmenuicon":
        context.user_data["admin_state"] = None
        context.user_data.pop("admin_temp", None)
        custom_emoji_id = None
        if update.message.entities:
            for entity in update.message.entities:
                if entity.type == "custom_emoji" and getattr(entity, "custom_emoji_id", None):
                    custom_emoji_id = entity.custom_emoji_id
                    break
        current = get_button_label_override("nav:back_menu") or {}
        if custom_emoji_id:
            set_button_label_override(
                "nav:back_menu",
                label=current.get("label"),
                icon_emoji=None,
                icon_custom_emoji_id=custom_emoji_id,
            )
            msg = "✅ Back to Menu ka premium/custom emoji icon set ho gaya."
        else:
            set_button_label_override(
                "nav:back_menu",
                label=current.get("label"),
                icon_emoji=text.strip(),
                icon_custom_emoji_id=None,
            )
            msg = f"✅ Back to Menu ka icon set ho gaya: {text.strip()}"
        await safe_reply_text(update.message, msg + "\n\nNaam change karne ke liye Edit Button Name → Navigation → Back to Menu use karein.", reply_markup=admin_back_keyboard())
        return

    if admin_state == "editbuttonname":
        context.user_data["admin_state"] = None
        temp = context.user_data.get("admin_temp", {})
        key = temp.get("button_key")
        group = temp.get("group")
        default_label = BUTTON_LABELS.get(key, key)

        custom_emoji_id = None
        custom_emoji_span = None
        if update.message.entities:
            for entity in update.message.entities:
                if entity.type == "custom_emoji" and getattr(entity, "custom_emoji_id", None):
                    custom_emoji_id = entity.custom_emoji_id
                    custom_emoji_span = (entity.offset, entity.offset + entity.length)
                    break

        if group == "Item Lists":
            # Icon-only: this key covers many different per-item buttons, so only
            # the emoji (normal or premium/custom) is stored — never the full text.
            if custom_emoji_id:
                set_button_label_override(key, label=None, icon_emoji=None, icon_custom_emoji_id=custom_emoji_id)
                await safe_reply_text(update.message, 
                    f"✅ {default_label} — premium/custom emoji icon set ho gaya (in sabhi buttons pe lagega).\n"
                    "⚠️ Ye tabhi dikhega jab bot banane wale account par Telegram Premium active ho.",
                    reply_markup=admin_back_keyboard(),
                )
            else:
                set_button_label_override(key, label=None, icon_emoji=text.strip(), icon_custom_emoji_id=None)
                await safe_reply_text(update.message, f"✅ {default_label} — icon set ho gaya: {text.strip()}", reply_markup=admin_back_keyboard())
            return

        # Regular single button: full text can be renamed. A custom/premium emoji can't be
        # embedded inside plain button text, so if one was sent, pull it out as a separate
        # icon and keep whatever other text was typed alongside it as the label.
        if custom_emoji_id:
            start, end = custom_emoji_span
            remaining = (text[:start] + text[end:]).strip()
            set_button_label_override(key, label=remaining or None, icon_emoji=None, icon_custom_emoji_id=custom_emoji_id)
            await safe_reply_text(update.message, 
                f"✅ {default_label} update ho gaya (premium/custom emoji icon set ho gaya).\n"
                "⚠️ Ye emoji tabhi dikhega jab bot banane wale account par Telegram Premium active ho.",
                reply_markup=admin_back_keyboard(),
            )
        else:
            set_button_label_override(key, label=text.strip(), icon_emoji=None, icon_custom_emoji_id=None)
            await safe_reply_text(update.message, f"✅ Button ka naya naam set ho gaya: {text.strip()}", reply_markup=admin_back_keyboard())
        return

    if admin_state == "addcustombtn_label":
        context.user_data["admin_state"] = "addcustombtn_choosetype"
        context.user_data["admin_temp"] = {"label": text.strip()}
        await safe_reply_text(update.message, 
            "Ye button tap karne par kya hoga?",
            reply_markup=custom_button_type_keyboard(),
        )
        return

    if admin_state == "addcustombtn_value":
        temp = context.user_data.get("admin_temp", {})
        label = temp.get("label")
        action_type = temp.get("action_type")
        if not label or not action_type:
            context.user_data["admin_state"] = None
            await safe_reply_text(update.message, "⚠️ Kuch galat ho gaya, dobara Admin Panel se try karein.", reply_markup=admin_back_keyboard())
            return
        value = text.strip()
        if action_type == "url" and not (value.startswith("http://") or value.startswith("https://") or value.startswith("t.me/") or value.startswith("@")):
            await safe_reply_text(update.message, "⚠️ Sahi URL bhejein (http:// ya https:// se shuru honi chahiye).")
            return
        if action_type == "url" and value.startswith("@"):
            value = f"https://t.me/{value[1:]}"
        elif action_type == "url" and value.startswith("t.me/"):
            value = f"https://{value}"
        context.user_data["admin_state"] = None
        add_custom_button(label, action_type, value)
        await safe_reply_text(update.message, f"✅ Naya button add ho gaya: {label}\nAb ye Main Menu mein dikhega.", reply_markup=admin_back_keyboard())
        return

    if admin_state == "broadcast":
        context.user_data["admin_state"] = None
        await safe_reply_text(update.message, f"⏳ Broadcast bhej raha hoon {len(list_all_user_ids())} users ko...")
        await run_broadcast(
            context, update.effective_chat.id,
            source_chat_id=update.effective_chat.id,
            source_message_id=update.message.message_id,
        )
        return

    if admin_state == "editTextBlock":
        context.user_data["admin_state"] = None
        key = context.user_data.get("admin_temp", {}).get("block_key")
        label = TEXT_BLOCK_LABELS.get(key, key)
        message_entities = extract_message_entities(update.message.entities)
        entities_json = entities_to_json(message_entities)
        set_text_block(key, text, entities_json)
        await safe_reply_text(update.message,
            f"✅ {label} update ho gaya — formatting/custom emoji preserve ho gaye.",
            reply_markup=manage_descriptions_keyboard(),
        )
        return

    if admin_state == "delivering_order":
        context.user_data["admin_state"] = None
        oid = context.user_data.get("admin_temp", {}).get("order_id")
        order = get_order(oid)
        if not order or order["status"] != "pending":
            await safe_reply_text(update.message, "Ye order ab pending nahi hai.", reply_markup=admin_back_keyboard())
            return
        set_order_status(oid, "delivered")
        set_order_delivery_info(oid, delivered_key=text, api_status="manual")
        try:
            receipt_text, receipt_kwargs = send_kwargs_for(format_delivery_receipt(order, text), reply_markup=delivery_receipt_keyboard(order))
            await safe_send_message(context.bot, chat_id=order["user_id"], text=receipt_text, **receipt_kwargs)
            await post_key_to_log_group(context, order, text)
            await safe_reply_text(update.message, f"✅ Order #{oid} customer ko deliver ho gaya.", reply_markup=admin_back_keyboard())
        except Exception as e:
            await safe_reply_text(update.message, f"⚠️ Customer ko message nahi bhej paya: {e}", reply_markup=admin_back_keyboard())
        return

    if admin_state == "delivering_order_android_id":
        context.user_data["admin_state"] = None
        oid = context.user_data.get("admin_temp", {}).get("order_id")
        order = get_order(oid)
        if not order or order["status"] != "pending":
            await safe_reply_text(update.message, "Ye order ab pending nahi hai.", reply_markup=admin_back_keyboard())
            return
        product = get_product(order["product_id"])
        android_id = text.strip()
        set_order_android_id(oid, android_id)
        duration = get_order_api_duration(order)

        status_msg = await safe_reply_text(update.message, "⏳ Reseller API se key fetch ho rahi hai...")
        result = await buy_key_for_product(product, order, oid)
        if result.get("success"):
            key_text = result["key"]
            set_order_status(oid, "delivered")
            set_order_delivery_info(oid, delivered_key=key_text, api_status="success")
            receipt_text, receipt_kwargs = send_kwargs_for(format_delivery_receipt(order, key_text), reply_markup=back_keyboard())
            await safe_send_message(context.bot, chat_id=order["user_id"], text=receipt_text, **receipt_kwargs)
            await post_key_to_log_group(context, order, key_text)
            await safe_reply_text(update.message, f"✅ Order #{oid} API se deliver ho gaya.", reply_markup=admin_back_keyboard())
        else:
            set_order_delivery_info(oid, delivered_key=None, api_status=f"failed: {result.get('error')}"[:500])
            buttons = IKM([
                [InlineKeyboardButton("✏️ Manual Deliver Karein", callback_data=f"delivermanual:{oid}")],
                [InlineKeyboardButton("⬅ Back", callback_data="admin:pendingorders")],
            ])
            await safe_reply_text(update.message, f"⚠️ API fail ho gaya:\n{result.get('error')}\n\nManual deliver karna chahenge?", reply_markup=buttons)
        return


# ---------------- Admin commands ----------------

async def add_product_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the exact same Add Product flow used by Admin Panel -> Add Product.

    The command intentionally reuses the existing ``admin_state=addproduct``
    message flow so /addproduct and the admin button cannot drift apart.
    Product description formatting/custom emoji handling, quick configuration,
    and all existing per-product options remain handled by the same code path.
    """
    if not is_admin(update.effective_user.id):
        await safe_reply_text(update.message, "⛔ Ye command sirf admin use kar sakta hai.")
        return

    context.user_data.pop("admin_state", None)
    context.user_data.pop("admin_temp", None)
    await safe_reply_text(
        update.message,
        "➕ *Add Product*\nReseller source chunein:",
        parse_mode="Markdown",
        reply_markup=IKM([
            [InlineKeyboardButton("🌐 ADD PRODUCT BANTI SITE", callback_data="admin:addproduct_banti")],
            [InlineKeyboardButton("🌐 ADD PRODUCT ADITYA SITE", callback_data="admin:addproduct_aditya")],
            [InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="admin:main")],
        ]),
    )


async def selling_proofs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Public /sellingproofs command. Shows an admin-editable description and
    two one-line channel buttons: Main Channel + Selling Proofs."""
    if get_setting("selling_proofs_enabled", "1") != "1":
        await safe_reply_text(update.message, "⚠️ Selling Proofs abhi available nahi hai.")
        return

    text, kwargs = send_kwargs_for(render_text_block("selling_proofs_text"))
    main_link = str(get_setting("update_channel", "") or "").strip()
    selling_link = str(get_setting("selling_proofs_link", "") or "").strip()

    rows = []
    row = []
    if main_link:
        row.append(InlineKeyboardButton("Main Channel", url=main_link))
    if selling_link:
        row.append(InlineKeyboardButton("Selling Proofs", url=selling_link))
    if row:
        rows.append(row)
    kwargs["reply_markup"] = IKM(rows) if rows else None
    await safe_reply_text(update.message, text, **kwargs)


async def remove_product_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await safe_reply_text(update.message, "⛔ Ye command sirf admin use kar sakta hai.")
        return
    if not context.args:
        await safe_reply_text(update.message, "Use: /removeproduct <product_id>")
        return
    try:
        pid = int(context.args[0])
    except ValueError:
        await safe_reply_text(update.message, "Product ID ek number hona chahiye.")
        return
    ok = remove_product(pid)
    await safe_reply_text(update.message, f"🗑️ Product ID {pid} remove ho gaya." if ok else "Ye product ID nahi mili.")


async def list_products_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await safe_reply_text(update.message, "⛔ Ye command sirf admin use kar sakta hai.")
        return
    products = list_products(active_only=False)
    if not products:
        await safe_reply_text(update.message, "Koi product nahi hai abhi.")
        return
    lines = []
    for p in products:
        plans = get_plans(p["id"])
        plans_str = ", ".join(f"{pl['label']} ₹{pl['price']:.0f}" for pl in plans) if plans else "no plans"
        lines.append(f"#{p['id']} | {p['name']} | {plans_str} | {'✅active' if p['active'] else '❌removed'}")
    await safe_reply_text(update.message, "\n".join(lines))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Logs unhandled errors and pings the first admin, so a bug never fails silently again.
    Conflict errors (same bot token running in 2 places) are throttled to one
    alert every 5 minutes instead of spamming on every retry. Transient network
    errors (WiFi/mobile data blips, connection drops mid-request) are just logged —
    the underlying library already retries these on its own, so pinging the admin
    for every brief disconnect would be noisy and isn't actionable."""
    is_network_hiccup = isinstance(context.error, (NetworkError, TimedOut, httpx.ReadError, httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError))
    if is_network_hiccup:
        logger.warning(f"Transient network error (auto-retried, no action needed): {context.error}")
        return
    logger.error("Unhandled error", exc_info=context.error)
    try:
        if not ADMIN_IDS:
            return
        if isinstance(context.error, Conflict):
            now = time.time()
            if now - main._last_conflict_alert < 300:
                return  # already alerted recently, don't spam
            main._last_conflict_alert = now
            await safe_send_message(context.bot, 
                chat_id=ADMIN_IDS[0],
                text=(
                    "⚠️ *Conflict error*: Isi bot token se kahin aur (doosra hosting/"
                    "local run/purana process) bhi polling chal rahi hai.\n\n"
                    "Fix: hosting panel mein saare purane/duplicate deployments band "
                    "karo, phir sirf ek jagah se bot start karo. Ye code ka bug nahi hai."
                ),
                parse_mode="Markdown",
            )
            return
        await safe_send_message(context.bot, 
            chat_id=ADMIN_IDS[0],
            text=f"⚠️ Bot mein ek error aaya:\n`{context.error}`",
            parse_mode="Markdown",
        )
    except Exception:
        pass


async def track_session_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track each unique private user seen during the current bot session."""
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    if user is None or chat is None:
        return
    if getattr(chat, "type", None) == "private":
        SESSION_USERS.add(int(user.id))


def live_session_user_count():
    """Unique private users seen since the current bot process started."""
    return len(SESSION_USERS)



async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the existing Broadcast flow from an admin-only command."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await safe_reply_text(update.message, "⛔ Ye command sirf admin use kar sakta hai.")
        return
    total = len(list_all_user_ids())
    context.user_data["admin_state"] = "broadcast"
    await safe_reply_text(
        update.message,
        f"📢 *Broadcast Message*\nAbhi total *{total}* users hain.\n\n"
        "Ab jo bhi message bhejenge — text, photo, video, voice, file ya sticker — "
        "wahi sab users ko bheja jayega, original formatting/emoji ke saath.",
        parse_mode="Markdown",
        reply_markup=admin_back_keyboard(),
    )


async def adminpanel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open the existing Admin Panel from an admin-only command."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await safe_reply_text(update.message, "⛔ Ye command sirf admin use kar sakta hai.")
        return
    await safe_reply_text(
        update.message,
        "⚙️ *Admin Panel*\nKya karna chahenge?",
        parse_mode="Markdown",
        reply_markup=admin_menu_keyboard(),
    )


async def add_balance_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the existing Add/Remove User Balance flow from an admin-only command."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await safe_reply_text(update.message, "⛔ Ye command sirf admin use kar sakta hai.")
        return
    context.user_data["admin_state"] = "addbalanceuser"
    await safe_reply_text(
        update.message,
        "💰 *Add / Remove User Balance*\n\n"
        "Format bhejein:\n"
        "`+20 123456789` → ₹20 add\n"
        "`-20 123456789` → ₹20 minus\n\n"
        "Username bhi use kar sakte hain:\n"
        "`+20 @username` ya `-20 @username`",
        parse_mode="Markdown",
        reply_markup=admin_back_keyboard(),
    )


async def post_init(application: Application):
    """Sets public commands and admin-only commands, and keeps the public bot name clean."""
    public_commands = [
        BotCommand("start", "Welcome / Main Menu"),
        BotCommand("shop", "Quick purchase"),
        BotCommand("support", "Get support"),
    ]
    admin_commands = public_commands + [
        BotCommand("adminpanel", "Open Admin Panel"),
        BotCommand("broadcast", "Broadcast Message"),
        BotCommand("addproduct", "Add Product"),
        BotCommand("addbalanceuser", "Add Balance User"),
    ]

    await application.bot.set_my_commands(public_commands)

    # Admin-only command menu: these extra commands are shown only in admin chats.
    for admin_id in get_all_admin_ids():
        try:
            await application.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception as e:
            logger.warning(f"Could not set admin command menu for {admin_id}: {e}")

    # Keep the public bot name as just the store name.
    try:
        await application.bot.set_my_name(name=STORE_NAME)
    except Exception as e:
        logger.warning(f"Could not set public bot name: {e}")



# --- Product/category navigation helpers ---
def category_products_callback(category):
    return f"products:{category}"

def product_price_callback(product_id):
    return f"product:{product_id}"

def product_list_back_callback(category):
    return f"category:{category}"

def price_list_back_callback(category):
    return f"products:{category}"


def main():
    init_db()
    main._last_conflict_alert = 0
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    application.add_handler(TypeHandler(Update, track_session_user), group=-1)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("shop", shop_cmd))
    application.add_handler(CommandHandler("support", support_cmd))
    application.add_handler(CommandHandler("cancel", cancel_cmd))
    application.add_handler(CommandHandler("adminpanel", adminpanel_cmd))
    application.add_handler(CommandHandler("broadcast", broadcast_cmd))
    application.add_handler(CommandHandler("addproduct", add_product_cmd))
    application.add_handler(CommandHandler("sellingproofs", selling_proofs_cmd))
    application.add_handler(CommandHandler("addbalanceuser", add_balance_user_cmd))
    application.add_handler(CommandHandler("removeproduct", remove_product_cmd))
    application.add_handler(CommandHandler("listproducts", list_products_cmd))
    application.add_handler(CommandHandler("groupid", groupid_cmd))
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, photo_handler))
    application.add_handler(MessageHandler(filters.VIDEO & filters.ChatType.PRIVATE, video_handler))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, text_handler))
    # Catch every other private message type during Broadcast (voice/audio/file/etc.).
    application.add_handler(MessageHandler(filters.ALL & filters.ChatType.PRIVATE, broadcast_any_message_handler))
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, sms_relay_handler))
    application.add_error_handler(error_handler)

    logger.info("AMAN OFC STORE bot started...")
    # drop_pending_updates + delete_webhook (done internally by run_polling) clears
    # any leftover webhook, which is one common cause of Conflict errors.
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
