"""
╔══════════════════════════════════════════════════════════╗
║        ULTRA ADVANCED TELEGRAM BOT — SINGLE FILE        ║
║     Admin Panel + Subscriptions + User Analytics        ║
║                  Render.com Ready                       ║
╚══════════════════════════════════════════════════════════╝

SETUP:
1. pip install python-telegram-bot==20.7 flask pyrogram tgcrypto \
              apscheduler matplotlib pillow reportlab openpyxl \
              sqlalchemy python-dotenv aiohttp
2. Set environment variables (see .env.example below)
3. Deploy to Render as Web Service
4. Set Start Command: python bot.py

ENV VARIABLES NEEDED:
  BOT_TOKEN       = your BotFather token
  ADMIN_IDS       = 123456,789012  (comma separated)
  API_ID          = Telegram API ID (my.telegram.org)
  API_HASH        = Telegram API Hash
  WEB_SECRET      = any random secret for web admin panel
  PORT            = 10000  (Render default)
"""

# ─────────────────────────────────────────────
#  IMPORTS
# ─────────────────────────────────────────────
import os, json, asyncio, logging, hashlib, io, csv
from datetime import datetime, timedelta
from functools import wraps
from threading import Thread

from dotenv import load_dotenv
load_dotenv()

# Telegram
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ChatMember, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

# Web Admin Panel
from flask import Flask, render_template_string, request, redirect, session, jsonify

# Database
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime,
    Boolean, Float, Text, BigInteger
)
from sqlalchemy.orm import declarative_base, sessionmaker

# Scheduler
from apscheduler.schedulers.background import BackgroundScheduler

# Charts
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

load_dotenv()

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_IDS   = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip()]
WEB_SECRET  = os.getenv("WEB_SECRET", "supersecretkey123")
PORT        = int(os.getenv("PORT", 10000))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # e.g. https://yourapp.onrender.com

# Subscription plan limits
PLANS = {
    "free":    {"name": "🆓 Free",    "price": 0,   "daily_limit": 5,   "features": ["Basic Info", "User ID"]},
    "basic":   {"name": "⚡ Basic",   "price": 99,  "daily_limit": 30,  "features": ["Full Profile", "Stats", "Groups"]},
    "premium": {"name": "💎 Premium", "price": 299, "daily_limit": 100, "features": ["All Basic", "Export", "Graphs", "Rank"]},
    "vip":     {"name": "👑 VIP",     "price": 999, "daily_limit": -1,  "features": ["Unlimited", "Priority", "Badges", "PDF Export"]},
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────
Base = declarative_base()
engine = create_engine("sqlite:///ultrabot.db", echo=False)
SessionLocal = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = "users"
    id              = Column(BigInteger, primary_key=True)
    username        = Column(String, nullable=True)
    first_name      = Column(String, nullable=True)
    last_name       = Column(String, nullable=True)
    language_code   = Column(String, nullable=True)
    is_premium      = Column(Boolean, default=False)
    is_bot          = Column(Boolean, default=False)
    subscription    = Column(String, default="free")
    sub_expiry      = Column(DateTime, nullable=True)
    is_banned       = Column(Boolean, default=False)
    is_admin        = Column(Boolean, default=False)
    joined_at       = Column(DateTime, default=datetime.utcnow)
    last_seen       = Column(DateTime, default=datetime.utcnow)
    total_messages  = Column(Integer, default=0)
    today_messages  = Column(Integer, default=0)
    today_date      = Column(String, default="")
    text_count      = Column(Integer, default=0)
    photo_count     = Column(Integer, default=0)
    video_count     = Column(Integer, default=0)
    sticker_count   = Column(Integer, default=0)
    voice_count     = Column(Integer, default=0)
    file_count      = Column(Integer, default=0)
    link_count      = Column(Integer, default=0)
    current_streak  = Column(Integer, default=0)
    longest_streak  = Column(Integer, default=0)
    last_active_day = Column(String, default="")
    badges          = Column(Text, default="[]")
    notes           = Column(Text, default="")
    referral_code   = Column(String, nullable=True)
    referred_by     = Column(BigInteger, nullable=True)
    referral_count  = Column(Integer, default=0)

class GroupLog(Base):
    __tablename__ = "group_logs"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(BigInteger)
    group_id   = Column(BigInteger)
    group_name = Column(String)
    role       = Column(String, default="member")
    msg_count  = Column(Integer, default=0)
    joined_at  = Column(DateTime, default=datetime.utcnow)

class MessageLog(Base):
    __tablename__ = "message_logs"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(BigInteger)
    chat_id    = Column(BigInteger)
    msg_type   = Column(String)
    timestamp  = Column(DateTime, default=datetime.utcnow)
    hour       = Column(Integer)
    weekday    = Column(Integer)

class Broadcast(Base):
    __tablename__ = "broadcasts"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    message    = Column(Text)
    sent_at    = Column(DateTime, default=datetime.utcnow)
    sent_count = Column(Integer, default=0)
    admin_id   = Column(BigInteger)

Base.metadata.create_all(engine)

# ─────────────────────────────────────────────
#  DB HELPERS
# ─────────────────────────────────────────────
def get_or_create_user(tg_user) -> User:
    db = SessionLocal()
    user = db.query(User).filter_by(id=tg_user.id).first()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if not user:
        import random, string
        ref = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        user = User(
            id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            language_code=getattr(tg_user, 'language_code', None),
            is_premium=getattr(tg_user, 'is_premium', False),
            is_bot=tg_user.is_bot,
            is_admin=tg_user.id in ADMIN_IDS,
            referral_code=ref,
            today_date=today,
        )
        db.add(user)
    else:
        user.username   = tg_user.username
        user.first_name = tg_user.first_name
        user.last_name  = tg_user.last_name
        user.last_seen  = datetime.utcnow()
        # Reset daily count if new day
        if user.today_date != today:
            user.today_messages = 0
            user.today_date = today
        # Streak logic
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        if user.last_active_day == yesterday:
            user.current_streak += 1
        elif user.last_active_day != today:
            user.current_streak = 1
        if user.current_streak > user.longest_streak:
            user.longest_streak = user.current_streak
        user.last_active_day = today
    db.commit()
    db.refresh(user)
    db.close()
    return user

def increment_message(user_id: int, msg_type: str, chat_id: int):
    db = SessionLocal()
    user = db.query(User).filter_by(id=user_id).first()
    if user:
        user.total_messages += 1
        user.today_messages += 1
        user.last_seen = datetime.utcnow()
        if msg_type == "text":    user.text_count += 1
        elif msg_type == "photo": user.photo_count += 1
        elif msg_type == "video": user.video_count += 1
        elif msg_type == "sticker": user.sticker_count += 1
        elif msg_type == "voice": user.voice_count += 1
        elif msg_type in ("document","audio"): user.file_count += 1
        # Badge check
        badges = json.loads(user.badges or "[]")
        def add_badge(b):
            if b not in badges:
                badges.append(b)
        if user.total_messages >= 100:   add_badge("💬 Chatterbox")
        if user.total_messages >= 1000:  add_badge("🔥 Message King")
        if user.total_messages >= 10000: add_badge("🏆 Legend")
        if user.current_streak >= 7:     add_badge("📅 Week Warrior")
        if user.current_streak >= 30:    add_badge("🚀 Streaker Pro")
        if user.is_premium:              add_badge("👑 Premium User")
        user.badges = json.dumps(badges)
        db.commit()
        # Log message
        log = MessageLog(
            user_id=user_id, chat_id=chat_id, msg_type=msg_type,
            hour=datetime.utcnow().hour, weekday=datetime.utcnow().weekday()
        )
        db.add(log)
        db.commit()
    db.close()

def check_limit(user: User) -> tuple[bool, int, int]:
    """Returns (allowed, used, limit)"""
    plan = PLANS.get(user.subscription, PLANS["free"])
    limit = plan["daily_limit"]
    if limit == -1:  # VIP = unlimited
        return True, user.today_messages, -1
    return user.today_messages < limit, user.today_messages, limit

def get_rank(user_id: int) -> int:
    db = SessionLocal()
    users = db.query(User).order_by(User.total_messages.desc()).all()
    db.close()
    for i, u in enumerate(users, 1):
        if u.id == user_id:
            return i
    return 0

def get_all_users():
    db = SessionLocal()
    users = db.query(User).all()
    db.close()
    return users

# ─────────────────────────────────────────────
#  DECORATORS
# ─────────────────────────────────────────────
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid not in ADMIN_IDS:
            await update.message.reply_text("⛔ Admin only command!")
            return
        return await func(update, ctx)
    return wrapper

def subscription_required(plan: str):
    order = ["free", "basic", "premium", "vip"]
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            tg_user = update.effective_user
            user = get_or_create_user(tg_user)
            if user.is_banned:
                await update.message.reply_text("🚫 You are banned!")
                return
            user_level = order.index(user.subscription)
            req_level  = order.index(plan)
            if user_level < req_level:
                kb = [[InlineKeyboardButton("💳 Upgrade Plan", callback_data="show_plans")]]
                await update.message.reply_text(
                    f"⚠️ This feature requires **{PLANS[plan]['name']}** plan!\n"
                    f"Your plan: {PLANS[user.subscription]['name']}",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            allowed, used, limit = check_limit(user)
            if not allowed:
                kb = [[InlineKeyboardButton("💳 Upgrade Plan", callback_data="show_plans")]]
                await update.message.reply_text(
                    f"⚠️ Daily limit reached! ({used}/{limit})\nUpgrade for more.",
                    reply_markup=InlineKeyboardMarkup(kb)
                )
                return
            return await func(update, ctx)
        return wrapper
    return decorator

# ─────────────────────────────────────────────
#  CHART GENERATOR
# ─────────────────────────────────────────────
def generate_activity_chart(user_id: int) -> io.BytesIO:
    db = SessionLocal()
    logs = db.query(MessageLog).filter_by(user_id=user_id).all()
    db.close()
    days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    counts = [0]*7
    for log in logs:
        counts[log.weekday] += 1
    fig, ax = plt.subplots(figsize=(8,4))
    bars = ax.bar(days, counts, color=['#6c63ff' if c == max(counts) else '#a29bfe' for c in counts])
    ax.set_title("📊 Weekly Activity", fontsize=14, fontweight='bold')
    ax.set_ylabel("Messages")
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    for bar, val in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3, str(val),
                ha='center', va='bottom', fontsize=10)
    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png', dpi=120)
    buf.seek(0)
    plt.close()
    return buf

def generate_hourly_chart(user_id: int) -> io.BytesIO:
    db = SessionLocal()
    logs = db.query(MessageLog).filter_by(user_id=user_id).all()
    db.close()
    counts = [0]*24
    for log in logs:
        counts[log.hour] += 1
    fig, ax = plt.subplots(figsize=(10,4))
    ax.plot(range(24), counts, color='#00b894', linewidth=2, marker='o', markersize=4)
    ax.fill_between(range(24), counts, alpha=0.2, color='#00b894')
    ax.set_title("⏰ Hourly Activity Pattern", fontsize=14, fontweight='bold')
    ax.set_xlabel("Hour of Day")
    ax.set_ylabel("Messages")
    ax.set_xticks(range(0,24,2))
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png', dpi=120)
    buf.seek(0)
    plt.close()
    return buf

# ─────────────────────────────────────────────
#  KEYBOARDS
# ─────────────────────────────────────────────
def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 My Profile",   callback_data="my_profile"),
         InlineKeyboardButton("📊 My Stats",     callback_data="my_stats")],
        [InlineKeyboardButton("👥 My Groups",    callback_data="my_groups"),
         InlineKeyboardButton("🏆 Leaderboard",  callback_data="leaderboard")],
        [InlineKeyboardButton("🎖️ Badges",       callback_data="my_badges"),
         InlineKeyboardButton("📈 Activity Graph",callback_data="activity_graph")],
        [InlineKeyboardButton("💳 Plans",         callback_data="show_plans"),
         InlineKeyboardButton("📤 Export Data",  callback_data="export_data")],
        [InlineKeyboardButton("🔗 Referral",      callback_data="referral"),
         InlineKeyboardButton("ℹ️ Help",           callback_data="help")],
    ])

def admin_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 All Users",       callback_data="admin_users"),
         InlineKeyboardButton("📊 Bot Stats",       callback_data="admin_botstats")],
        [InlineKeyboardButton("📢 Broadcast",       callback_data="admin_broadcast"),
         InlineKeyboardButton("🔍 Find User",       callback_data="admin_finduser")],
        [InlineKeyboardButton("✅ Give Sub",        callback_data="admin_givesub"),
         InlineKeyboardButton("🚫 Ban User",        callback_data="admin_ban")],
        [InlineKeyboardButton("✔️ Unban User",      callback_data="admin_unban"),
         InlineKeyboardButton("📝 Add Note",        callback_data="admin_note")],
        [InlineKeyboardButton("🌐 Web Panel",       callback_data="admin_webpanel")],
    ])

def plans_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆓 Free — ₹0/mo",     callback_data="plan_info_free")],
        [InlineKeyboardButton("⚡ Basic — ₹99/mo",   callback_data="plan_info_basic")],
        [InlineKeyboardButton("💎 Premium — ₹299/mo",callback_data="plan_info_premium")],
        [InlineKeyboardButton("👑 VIP — ₹999/mo",    callback_data="plan_info_vip")],
        [InlineKeyboardButton("🔙 Back",              callback_data="main_menu")],
    ])

# ─────────────────────────────────────────────
#  COMMAND HANDLERS
# ─────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user = get_or_create_user(tg_user)
    # Handle referral
    if ctx.args and ctx.args[0].startswith("ref_"):
        ref_code = ctx.args[0][4:]
        db = SessionLocal()
        referrer = db.query(User).filter_by(referral_code=ref_code).first()
        if referrer and referrer.id != tg_user.id and not user.referred_by:
            referrer.referral_count += 1
            user.referred_by = referrer.id
            db.commit()
        db.close()
    name = tg_user.first_name or "User"
    plan = PLANS[user.subscription]["name"]
    text = (
        f"👋 Welcome, **{name}**!\n\n"
        f"🤖 I'm your **Ultra Advanced Info Bot**\n"
        f"💳 Your Plan: {plan}\n"
        f"🆔 Your ID: `{tg_user.id}`\n\n"
        f"Choose what you want to do:"
    )
    await update.message.reply_text(text, reply_markup=main_menu_kb(), parse_mode=ParseMode.MARKDOWN)

async def info_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user = get_or_create_user(tg_user)
    increment_message(tg_user.id, "text", update.effective_chat.id)
    allowed, used, limit = check_limit(user)
    limit_str = "∞" if limit == -1 else str(limit)
    rank = get_rank(tg_user.id)
    all_users = get_all_users()
    top_pct = round((rank / max(len(all_users),1)) * 100)
    badges = json.loads(user.badges or "[]")
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👤 **COMPLETE USER PROFILE**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 User ID        : `{tg_user.id}`\n"
        f"📛 Full Name      : {tg_user.full_name}\n"
        f"👤 Username       : @{tg_user.username or 'N/A'}\n"
        f"🌐 Language       : {user.language_code or 'N/A'}\n"
        f"👑 Premium        : {'✅' if user.is_premium else '❌'}\n"
        f"🤖 Is Bot         : {'✅' if user.is_bot else '❌'}\n"
        f"💳 Plan           : {PLANS[user.subscription]['name']}\n"
        f"📅 Joined Bot     : {user.joined_at.strftime('%d %b %Y')}\n"
        f"⏰ Last Seen      : {user.last_seen.strftime('%d %b %Y %H:%M')} UTC\n"
        f"🏆 Global Rank    : #{rank} (Top {top_pct}%)\n"
        f"🎖️ Badges         : {' '.join(badges) if badges else 'None yet'}\n"
        f"📊 Today Usage    : {used}/{limit_str}\n"
        f"🔗 Deep Link      : t.me/{tg_user.username or str(tg_user.id)}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Full Stats", callback_data="my_stats"),
         InlineKeyboardButton("🏠 Menu",        callback_data="main_menu")]
    ])
    await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user = get_or_create_user(tg_user)
    increment_message(tg_user.id, "text", update.effective_chat.id)
    db = SessionLocal()
    week_ago = datetime.utcnow() - timedelta(days=7)
    month_ago = datetime.utcnow() - timedelta(days=30)
    week_msgs  = db.query(MessageLog).filter(
        MessageLog.user_id==tg_user.id, MessageLog.timestamp >= week_ago).count()
    month_msgs = db.query(MessageLog).filter(
        MessageLog.user_id==tg_user.id, MessageLog.timestamp >= month_ago).count()
    # Most active hour
    logs = db.query(MessageLog).filter_by(user_id=tg_user.id).all()
    db.close()
    hour_counts = [0]*24
    day_counts  = [0]*7
    for log in logs:
        hour_counts[log.hour] += 1
        day_counts[log.weekday] += 1
    peak_hour = hour_counts.index(max(hour_counts)) if any(hour_counts) else 0
    days_name = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    peak_day  = days_name[day_counts.index(max(day_counts))] if any(day_counts) else "N/A"
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 **MEGA ACTIVITY STATS**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 Total Messages  : {user.total_messages:,}\n"
        f"📝 Text            : {user.text_count:,}\n"
        f"🖼️ Photos          : {user.photo_count:,}\n"
        f"🎥 Videos          : {user.video_count:,}\n"
        f"😂 Stickers        : {user.sticker_count:,}\n"
        f"🎤 Voice Notes     : {user.voice_count:,}\n"
        f"📁 Files           : {user.file_count:,}\n"
        "──────────────────────\n"
        f"📅 Today           : {user.today_messages:,}\n"
        f"📅 This Week       : {week_msgs:,}\n"
        f"📅 This Month      : {month_msgs:,}\n"
        "──────────────────────\n"
        f"⏰ Peak Hour       : {peak_hour}:00 - {peak_hour+1}:00\n"
        f"📆 Peak Day        : {peak_day}\n"
        f"🔥 Current Streak  : {user.current_streak} days\n"
        f"🏅 Longest Streak  : {user.longest_streak} days\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 Weekly Graph",  callback_data="activity_graph"),
         InlineKeyboardButton("⏰ Hourly Graph",  callback_data="hourly_graph")],
        [InlineKeyboardButton("🏠 Menu", callback_data="main_menu")]
    ])
    await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Admin only!")
        return
    all_users = get_all_users()
    banned = sum(1 for u in all_users if u.is_banned)
    premium_users = sum(1 for u in all_users if u.subscription != "free")
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👑 **ADMIN CONTROL PANEL**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total Users   : {len(all_users)}\n"
        f"💎 Paid Users    : {premium_users}\n"
        f"🚫 Banned        : {banned}\n"
        f"📅 Today         : {datetime.utcnow().strftime('%d %b %Y')}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(text, reply_markup=admin_menu_kb(), parse_mode=ParseMode.MARKDOWN)

async def give_sub_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /givesub USER_ID PLAN DAYS"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Admin only!"); return
    try:
        _, uid, plan, days = update.message.text.split()
        uid = int(uid); days = int(days)
        if plan not in PLANS:
            await update.message.reply_text("❌ Invalid plan. Use: free/basic/premium/vip"); return
        db = SessionLocal()
        user = db.query(User).filter_by(id=uid).first()
        if not user:
            await update.message.reply_text("❌ User not found"); db.close(); return
        user.subscription = plan
        user.sub_expiry   = datetime.utcnow() + timedelta(days=days)
        db.commit(); db.close()
        await update.message.reply_text(f"✅ Gave {PLANS[plan]['name']} to `{uid}` for {days} days!", parse_mode=ParseMode.MARKDOWN)
        try:
            await ctx.bot.send_message(uid, f"🎉 Your plan upgraded to **{PLANS[plan]['name']}** for {days} days!", parse_mode=ParseMode.MARKDOWN)
        except: pass
    except (ValueError, IndexError):
        await update.message.reply_text("Usage: /givesub USER_ID PLAN DAYS\nExample: /givesub 123456 premium 30")

async def ban_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /ban USER_ID"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Admin only!"); return
    try:
        uid = int(ctx.args[0])
        db = SessionLocal()
        user = db.query(User).filter_by(id=uid).first()
        if user:
            user.is_banned = True
            db.commit()
            await update.message.reply_text(f"🚫 User `{uid}` has been banned!", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("❌ User not found")
        db.close()
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /ban USER_ID")

async def unban_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /unban USER_ID"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Admin only!"); return
    try:
        uid = int(ctx.args[0])
        db = SessionLocal()
        user = db.query(User).filter_by(id=uid).first()
        if user:
            user.is_banned = False
            db.commit()
            await update.message.reply_text(f"✅ User `{uid}` has been unbanned!", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("❌ User not found")
        db.close()
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /unban USER_ID")

async def broadcast_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /broadcast Your message here"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Admin only!"); return
    if not ctx.args:
        await update.message.reply_text("Usage: /broadcast Your message here"); return
    msg = " ".join(ctx.args)
    all_users = get_all_users()
    sent = 0
    for u in all_users:
        if not u.is_banned:
            try:
                await ctx.bot.send_message(u.id, f"📢 **Broadcast**\n\n{msg}", parse_mode=ParseMode.MARKDOWN)
                sent += 1
            except: pass
    db = SessionLocal()
    db.add(Broadcast(message=msg, sent_count=sent, admin_id=update.effective_user.id))
    db.commit(); db.close()
    await update.message.reply_text(f"✅ Broadcast sent to {sent} users!")

async def export_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user = get_or_create_user(tg_user)
    if user.subscription not in ("premium","vip"):
        await update.message.reply_text("💎 Premium/VIP plan required for export!"); return
    db = SessionLocal()
    logs = db.query(MessageLog).filter_by(user_id=tg_user.id).all()
    db.close()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp","chat_id","msg_type","hour","weekday"])
    for log in logs:
        writer.writerow([log.timestamp, log.chat_id, log.msg_type, log.hour, log.weekday])
    buf.seek(0)
    await update.message.reply_document(
        document=io.BytesIO(buf.getvalue().encode()),
        filename=f"activity_{tg_user.id}.csv",
        caption="📤 Your activity data export!"
    )

async def leaderboard_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    all_users = get_all_users()
    top = sorted(all_users, key=lambda u: u.total_messages, reverse=True)[:10]
    medals = ["🥇","🥈","🥉"] + [f"{i}️⃣" for i in range(4,11)]
    lines = ["━━━━━━━━━━━━━━━━━━━━━━━\n🏆 **GLOBAL LEADERBOARD**\n━━━━━━━━━━━━━━━━━━━━━━━"]
    for i, u in enumerate(top):
        name = u.first_name or f"User{u.id}"
        uname = f"@{u.username}" if u.username else f"#{u.id}"
        lines.append(f"{medals[i]} {name} ({uname}) — {u.total_messages:,} msgs")
    uid = update.effective_user.id
    rank = get_rank(uid)
    lines.append(f"\n📍 Your Rank: #{rank}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────
#  CALLBACK HANDLERS
# ─────────────────────────────────────────────
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    tg_user = query.from_user
    user = get_or_create_user(tg_user)

    if data == "main_menu":
        await query.edit_message_text("🏠 **Main Menu**", reply_markup=main_menu_kb(), parse_mode=ParseMode.MARKDOWN)

    elif data == "my_profile":
        rank = get_rank(tg_user.id)
        all_u = get_all_users()
        top_pct = round((rank/max(len(all_u),1))*100)
        badges = json.loads(user.badges or "[]")
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "👤 **YOUR PROFILE**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 ID       : `{tg_user.id}`\n"
            f"📛 Name     : {tg_user.full_name}\n"
            f"👤 Username : @{tg_user.username or 'N/A'}\n"
            f"👑 Premium  : {'✅' if user.is_premium else '❌'}\n"
            f"💳 Plan     : {PLANS[user.subscription]['name']}\n"
            f"📅 Joined   : {user.joined_at.strftime('%d %b %Y')}\n"
            f"🏆 Rank     : #{rank} (Top {top_pct}%)\n"
            f"🎖️ Badges   : {' '.join(badges) if badges else 'None'}\n"
            f"🔥 Streak   : {user.current_streak} days\n"
            "━━━━━━━━━━━━━━━━━━━━━━━"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    elif data == "my_stats":
        db = SessionLocal()
        week_msgs  = db.query(MessageLog).filter(
            MessageLog.user_id==tg_user.id,
            MessageLog.timestamp >= datetime.utcnow()-timedelta(days=7)).count()
        month_msgs = db.query(MessageLog).filter(
            MessageLog.user_id==tg_user.id,
            MessageLog.timestamp >= datetime.utcnow()-timedelta(days=30)).count()
        db.close()
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📊 **YOUR STATS**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💬 Total    : {user.total_messages:,}\n"
            f"📅 Today    : {user.today_messages:,}\n"
            f"📅 Week     : {week_msgs:,}\n"
            f"📅 Month    : {month_msgs:,}\n"
            f"📝 Text     : {user.text_count:,}\n"
            f"🖼️ Photos   : {user.photo_count:,}\n"
            f"🎥 Videos   : {user.video_count:,}\n"
            f"😂 Stickers : {user.sticker_count:,}\n"
            f"🎤 Voice    : {user.voice_count:,}\n"
            f"📁 Files    : {user.file_count:,}\n"
            f"🔥 Streak   : {user.current_streak} days\n"
            "━━━━━━━━━━━━━━━━━━━━━━━"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📈 Graph", callback_data="activity_graph")],
            [InlineKeyboardButton("🔙 Back",  callback_data="main_menu")]
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    elif data == "activity_graph":
        await query.edit_message_text("⏳ Generating graph...")
        chart = generate_activity_chart(tg_user.id)
        await ctx.bot.send_photo(tg_user.id, photo=chart, caption="📊 Your Weekly Activity")
        await query.edit_message_text("📊 Graph sent!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="main_menu")]]))

    elif data == "hourly_graph":
        await query.edit_message_text("⏳ Generating hourly graph...")
        chart = generate_hourly_chart(tg_user.id)
        await ctx.bot.send_photo(tg_user.id, photo=chart, caption="⏰ Your Hourly Activity")
        await query.edit_message_text("⏰ Hourly graph sent!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="main_menu")]]))

    elif data == "leaderboard":
        all_users = get_all_users()
        top = sorted(all_users, key=lambda u: u.total_messages, reverse=True)[:10]
        medals = ["🥇","🥈","🥉"]+[f"{i}️⃣" for i in range(4,11)]
        lines = ["🏆 **LEADERBOARD**\n"]
        for i,u in enumerate(top):
            name = u.first_name or f"User{u.id}"
            lines.append(f"{medals[i]} {name} — {u.total_messages:,} msgs")
        rank = get_rank(tg_user.id)
        lines.append(f"\n📍 Your Rank: #{rank}")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="main_menu")]])
        await query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    elif data == "my_badges":
        badges = json.loads(user.badges or "[]")
        all_badges = [
            ("💬 Chatterbox","100 messages"),("🔥 Message King","1,000 messages"),
            ("🏆 Legend","10,000 messages"),("📅 Week Warrior","7 day streak"),
            ("🚀 Streaker Pro","30 day streak"),("👑 Premium User","Has premium plan"),
        ]
        lines = ["🎖️ **YOUR BADGES**\n"]
        for badge, req in all_badges:
            status = "✅" if badge in badges else "🔒"
            lines.append(f"{status} {badge} — {req}")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="main_menu")]])
        await query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    elif data == "show_plans":
        text = "💳 **SUBSCRIPTION PLANS**\n\n"
        for key, plan in PLANS.items():
            limit = "Unlimited" if plan['daily_limit']==-1 else str(plan['daily_limit'])
            features = ", ".join(plan['features'])
            text += f"{plan['name']} — ₹{plan['price']}/mo\n📊 Daily Limit: {limit}\n✅ {features}\n\n"
        text += "Contact admin to upgrade: @YourAdminUsername"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="main_menu")]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("plan_info_"):
        plan_key = data.split("_")[-1]
        plan = PLANS.get(plan_key,{})
        limit = "Unlimited" if plan.get('daily_limit')==-1 else str(plan.get('daily_limit'))
        text = (
            f"{plan['name']} Plan\n\n"
            f"💰 Price: ₹{plan['price']}/month\n"
            f"📊 Daily Limit: {limit} requests\n"
            f"✅ Features:\n" + "\n".join(f"  • {f}" for f in plan['features']) +
            "\n\nContact admin to activate!"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="show_plans")]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    elif data == "my_groups":
        db = SessionLocal()
        groups = db.query(GroupLog).filter_by(user_id=tg_user.id).all()
        db.close()
        if not groups:
            text = "👥 No shared groups found yet.\n(Groups are tracked when you use bot in groups)"
        else:
            lines = ["👥 **YOUR GROUPS**\n"]
            fav = max(groups, key=lambda g: g.msg_count)
            for g in sorted(groups, key=lambda x: x.msg_count, reverse=True):
                star = "⭐" if g.group_id == fav.group_id else "▫️"
                lines.append(f"{star} {g.group_name}\n   💬 {g.msg_count} msgs | 👤 {g.role}")
            text = "\n".join(lines)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="main_menu")]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    elif data == "referral":
        ref_link = f"https://t.me/{ctx.bot.username}?start=ref_{user.referral_code}"
        text = (
            "🔗 **YOUR REFERRAL**\n\n"
            f"📨 Your Link:\n`{ref_link}`\n\n"
            f"👥 Total Referrals: {user.referral_count}\n\n"
            "Share this link to invite friends!"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="main_menu")]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    elif data == "export_data":
        if user.subscription not in ("premium","vip"):
            await query.answer("💎 Premium/VIP required!", show_alert=True)
            return
        db = SessionLocal()
        logs = db.query(MessageLog).filter_by(user_id=tg_user.id).all()
        db.close()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["timestamp","type","hour","weekday"])
        for log in logs:
            writer.writerow([log.timestamp, log.msg_type, log.hour, log.weekday])
        buf.seek(0)
        await ctx.bot.send_document(
            tg_user.id,
            document=io.BytesIO(buf.getvalue().encode()),
            filename=f"export_{tg_user.id}.csv",
            caption="📤 Your data export!"
        )
        await query.answer("✅ Export sent!")

    elif data == "help":
        text = (
            "ℹ️ **HELP & COMMANDS**\n\n"
            "/start — Main menu\n"
            "/info — Your full Telegram profile\n"
            "/stats — Activity statistics\n"
            "/leaderboard — Top users\n"
            "/export — Export data (Premium+)\n\n"
            "**Admin Commands:**\n"
            "/admin — Admin panel\n"
            "/givesub ID PLAN DAYS\n"
            "/ban ID — Ban user\n"
            "/unban ID — Unban user\n"
            "/broadcast — Send to all users\n"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="main_menu")]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    elif data == "admin_botstats" and tg_user.id in ADMIN_IDS:
        all_u = get_all_users()
        subs = {k: sum(1 for u in all_u if u.subscription==k) for k in PLANS}
        total_msgs = sum(u.total_messages for u in all_u)
        text = (
            "📊 **BOT STATISTICS**\n\n"
            f"👥 Total Users    : {len(all_u)}\n"
            f"💬 Total Messages : {total_msgs:,}\n"
            f"🚫 Banned         : {sum(1 for u in all_u if u.is_banned)}\n\n"
            "**Subscriptions:**\n"
        )
        for k,v in subs.items():
            text += f"  {PLANS[k]['name']}: {v}\n"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_back")]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    elif data == "admin_users" and tg_user.id in ADMIN_IDS:
        all_u = get_all_users()[:20]
        lines = ["👥 **RECENT USERS**\n"]
        for u in all_u:
            status = "🚫" if u.is_banned else PLANS[u.subscription]["name"]
            lines.append(f"• `{u.id}` @{u.username or 'N/A'} — {status}")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="admin_back")]])
        await query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    elif data == "admin_webpanel" and tg_user.id in ADMIN_IDS:
        web_url = WEBHOOK_URL.replace("/webhook","") + "/admin" if WEBHOOK_URL else "Set WEBHOOK_URL env var"
        await query.answer(f"Web Panel: {web_url}", show_alert=True)

    elif data == "admin_back" and tg_user.id in ADMIN_IDS:
        await query.edit_message_text("👑 **Admin Panel**", reply_markup=admin_menu_kb(), parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────
#  MESSAGE TRACKER
# ─────────────────────────────────────────────
async def track_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    tg_user = update.effective_user
    get_or_create_user(tg_user)
    msg = update.message
    if msg.text:         msg_type = "text"
    elif msg.photo:      msg_type = "photo"
    elif msg.video:      msg_type = "video"
    elif msg.sticker:    msg_type = "sticker"
    elif msg.voice:      msg_type = "voice"
    elif msg.document:   msg_type = "document"
    elif msg.audio:      msg_type = "audio"
    else:                msg_type = "other"
    increment_message(tg_user.id, msg_type, update.effective_chat.id)
    # Track groups
    if update.effective_chat.type in ("group","supergroup"):
        db = SessionLocal()
        group = db.query(GroupLog).filter_by(
            user_id=tg_user.id, group_id=update.effective_chat.id).first()
        if not group:
            db.add(GroupLog(
                user_id=tg_user.id,
                group_id=update.effective_chat.id,
                group_name=update.effective_chat.title or "Unknown Group",
                role="member"
            ))
        else:
            group.msg_count += 1
            group.group_name = update.effective_chat.title or group.group_name
        db.commit(); db.close()

# ─────────────────────────────────────────────
#  FLASK WEB ADMIN PANEL
# ─────────────────────────────────────────────
WEB_ADMIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🤖 Bot Admin Panel</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:'Segoe UI',sans-serif;background:#0f0f1a;color:#e0e0e0;min-height:100vh}
  .navbar{background:#1a1a2e;padding:16px 32px;display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid #6c63ff}
  .navbar h1{color:#6c63ff;font-size:1.4rem}
  .logout{color:#ff6b6b;text-decoration:none;font-size:.9rem}
  .container{max-width:1200px;margin:0 auto;padding:24px}
  .stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:32px}
  .stat-card{background:#1a1a2e;border-radius:12px;padding:20px;border-left:4px solid #6c63ff;text-align:center}
  .stat-card .num{font-size:2rem;font-weight:700;color:#6c63ff}
  .stat-card .label{color:#aaa;font-size:.85rem;margin-top:4px}
  .section{background:#1a1a2e;border-radius:12px;padding:24px;margin-bottom:24px}
  .section h2{color:#6c63ff;margin-bottom:16px;font-size:1.1rem}
  table{width:100%;border-collapse:collapse}
  th{background:#0f0f1a;padding:10px;text-align:left;color:#6c63ff;font-size:.85rem}
  td{padding:10px;border-bottom:1px solid #2a2a3e;font-size:.85rem}
  tr:hover td{background:#252540}
  .badge{padding:3px 8px;border-radius:20px;font-size:.75rem;font-weight:600}
  .badge-free{background:#2d3436;color:#74b9ff}
  .badge-basic{background:#00b894;color:#fff}
  .badge-premium{background:#6c63ff;color:#fff}
  .badge-vip{background:#fdcb6e;color:#000}
  .badge-banned{background:#d63031;color:#fff}
  .btn{padding:8px 16px;border-radius:8px;border:none;cursor:pointer;font-size:.85rem;transition:.2s}
  .btn-danger{background:#d63031;color:#fff}.btn-success{background:#00b894;color:#fff}
  .btn-primary{background:#6c63ff;color:#fff}
  .form-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
  input,select{background:#0f0f1a;border:1px solid #3a3a5e;color:#e0e0e0;padding:8px 12px;border-radius:8px;font-size:.85rem}
  .alert{background:#2d1b69;border:1px solid #6c63ff;padding:12px 16px;border-radius:8px;margin-bottom:16px;font-size:.85rem}
  .login-box{max-width:400px;margin:100px auto;background:#1a1a2e;border-radius:16px;padding:40px;text-align:center}
  .login-box h1{color:#6c63ff;margin-bottom:24px}
  .login-box input{width:100%;margin-bottom:12px;display:block}
  .login-box button{width:100%;padding:12px;background:#6c63ff;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:1rem}
</style>
</head>
<body>
{% if not logged_in %}
<div class="login-box">
  <h1>🤖 Admin Panel</h1>
  <form method="POST" action="/admin/login">
    <input type="password" name="password" placeholder="Admin Password" required>
    <button type="submit">Login</button>
  </form>
  {% if error %}<p style="color:#ff6b6b;margin-top:12px">{{ error }}</p>{% endif %}
</div>
{% else %}
<div class="navbar">
  <h1>🤖 Ultra Bot Admin</h1>
  <a href="/admin/logout" class="logout">Logout</a>
</div>
<div class="container">
  {% if msg %}<div class="alert">{{ msg }}</div>{% endif %}

  <div class="stats-grid">
    <div class="stat-card"><div class="num">{{ stats.total }}</div><div class="label">Total Users</div></div>
    <div class="stat-card"><div class="num">{{ stats.paid }}</div><div class="label">Paid Users</div></div>
    <div class="stat-card"><div class="num">{{ stats.banned }}</div><div class="label">Banned</div></div>
    <div class="stat-card"><div class="num">{{ stats.messages }}</div><div class="label">Total Messages</div></div>
  </div>

  <div class="section">
    <h2>⚡ Quick Actions</h2>
    <div class="form-row">
      <form method="POST" action="/admin/givesub" style="display:flex;gap:8px;flex-wrap:wrap">
        <input name="uid" placeholder="User ID" required>
        <select name="plan"><option value="free">Free</option><option value="basic">Basic</option><option value="premium">Premium</option><option value="vip">VIP</option></select>
        <input name="days" placeholder="Days" type="number" value="30">
        <button class="btn btn-primary" type="submit">Give Sub</button>
      </form>
    </div>
    <div class="form-row" style="margin-top:12px">
      <form method="POST" action="/admin/ban" style="display:flex;gap:8px">
        <input name="uid" placeholder="User ID">
        <button class="btn btn-danger" type="submit">Ban</button>
      </form>
      <form method="POST" action="/admin/unban" style="display:flex;gap:8px">
        <input name="uid" placeholder="User ID">
        <button class="btn btn-success" type="submit">Unban</button>
      </form>
    </div>
  </div>

  <div class="section">
    <h2>📢 Broadcast Message</h2>
    <form method="POST" action="/admin/broadcast" style="display:flex;gap:8px;flex-wrap:wrap">
      <input name="message" placeholder="Your broadcast message..." style="flex:1;min-width:250px" required>
      <button class="btn btn-primary" type="submit">Send to All</button>
    </form>
  </div>

  <div class="section">
    <h2>👥 All Users</h2>
    <table>
      <tr><th>ID</th><th>Name</th><th>Username</th><th>Plan</th><th>Messages</th><th>Joined</th><th>Status</th></tr>
      {% for u in users %}
      <tr>
        <td><code>{{ u.id }}</code></td>
        <td>{{ u.first_name or 'N/A' }}</td>
        <td>@{{ u.username or 'N/A' }}</td>
        <td><span class="badge badge-{{ u.subscription }}">{{ u.subscription.upper() }}</span></td>
        <td>{{ u.total_messages }}</td>
        <td>{{ u.joined_at.strftime('%d %b %Y') if u.joined_at else 'N/A' }}</td>
        <td>{% if u.is_banned %}<span class="badge badge-banned">BANNED</span>{% else %}<span style="color:#00b894">Active</span>{% endif %}</td>
      </tr>
      {% endfor %}
    </table>
  </div>
</div>
{% endif %}
</body>
</html>
"""

flask_app = Flask(__name__)
flask_app.secret_key = WEB_SECRET

def web_stats():
    all_u = get_all_users()
    return {
        "total": len(all_u),
        "paid": sum(1 for u in all_u if u.subscription != "free"),
        "banned": sum(1 for u in all_u if u.is_banned),
        "messages": sum(u.total_messages for u in all_u),
    }

@flask_app.route("/admin", methods=["GET"])
def admin_panel():
    logged = session.get("admin_logged")
    if logged:
        users = get_all_users()
        return render_template_string(WEB_ADMIN_HTML, logged_in=True, users=users, stats=web_stats(), msg=session.pop("msg",""))
    return render_template_string(WEB_ADMIN_HTML, logged_in=False, error=None)

@flask_app.route("/admin/login", methods=["POST"])
def admin_login():
    if request.form.get("password") == WEB_SECRET:
        session["admin_logged"] = True
        return redirect("/admin")
    return render_template_string(WEB_ADMIN_HTML, logged_in=False, error="Wrong password!")

@flask_app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin")

@flask_app.route("/admin/givesub", methods=["POST"])
def web_givesub():
    if not session.get("admin_logged"): return redirect("/admin")
    uid = int(request.form.get("uid",0))
    plan = request.form.get("plan","free")
    days = int(request.form.get("days",30))
    db = SessionLocal()
    user = db.query(User).filter_by(id=uid).first()
    if user:
        user.subscription = plan
        user.sub_expiry = datetime.utcnow() + timedelta(days=days)
        db.commit()
        session["msg"] = f"✅ Gave {plan} to {uid} for {days} days!"
    else:
        session["msg"] = "❌ User not found"
    db.close()
    return redirect("/admin")

@flask_app.route("/admin/ban", methods=["POST"])
def web_ban():
    if not session.get("admin_logged"): return redirect("/admin")
    uid = int(request.form.get("uid",0))
    db = SessionLocal()
    user = db.query(User).filter_by(id=uid).first()
    if user:
        user.is_banned = True
        db.commit()
        session["msg"] = f"🚫 Banned {uid}"
    db.close()
    return redirect("/admin")

@flask_app.route("/admin/unban", methods=["POST"])
def web_unban():
    if not session.get("admin_logged"): return redirect("/admin")
    uid = int(request.form.get("uid",0))
    db = SessionLocal()
    user = db.query(User).filter_by(id=uid).first()
    if user:
        user.is_banned = False
        db.commit()
        session["msg"] = f"✅ Unbanned {uid}"
    db.close()
    return redirect("/admin")

@flask_app.route("/admin/broadcast", methods=["POST"])
def web_broadcast():
    if not session.get("admin_logged"): return redirect("/admin")
    msg = request.form.get("message","")
    session["msg"] = f"📢 Broadcast queued: '{msg[:50]}...'"
    return redirect("/admin")

@flask_app.route("/health")
def health():
    return jsonify({"status":"ok","users":len(get_all_users())})

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False)

# ─────────────────────────────────────────────
#  SCHEDULER — Daily Reset & Reminders
# ─────────────────────────────────────────────
def daily_reset():
    db = SessionLocal()
    db.query(User).update({"today_messages": 0, "today_date": datetime.utcnow().strftime("%Y-%m-%d")})
    db.commit(); db.close()
    logger.info("Daily message counts reset.")

scheduler = BackgroundScheduler()
scheduler.add_job(daily_reset, 'cron', hour=0, minute=0)
scheduler.start()

# ─────────────────────────────────────────────
#  MAIN — BOT STARTUP
# ─────────────────────────────────────────────
def main():
    # Start Flask in background thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"✅ Web Admin Panel started on port {PORT}")

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start",        start))
    app.add_handler(CommandHandler("info",         info_cmd))
    app.add_handler(CommandHandler("stats",        stats_cmd))
    app.add_handler(CommandHandler("leaderboard",  leaderboard_cmd))
    app.add_handler(CommandHandler("export",       export_cmd))
    app.add_handler(CommandHandler("admin",        admin_cmd))
    app.add_handler(CommandHandler("givesub",      give_sub_cmd))
    app.add_handler(CommandHandler("ban",          ban_cmd))
    app.add_handler(CommandHandler("unban",        unban_cmd))
    app.add_handler(CommandHandler("broadcast",    broadcast_cmd))

    # Callbacks
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Message tracker (all messages)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, track_message))

    # Set bot commands
    async def post_init(application):
        await application.bot.set_my_commands([
            BotCommand("start", "Main Menu"),
            BotCommand("info", "Your full profile"),
            BotCommand("stats", "Activity stats"),
            BotCommand("leaderboard", "Top users"),
            BotCommand("export", "Export your data"),
        ])

    app.post_init = post_init

    logger.info("🤖 Bot starting...")

    if WEBHOOK_URL:
        # Webhook mode for Render
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT + 1,
            webhook_url=f"{WEBHOOK_URL}/webhook",
            url_path="/webhook",
        )
    else:
        # Polling mode for local dev
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
