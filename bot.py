import os
import sys
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# ضبط ترميز UTF-8 لموجه أوامر ويندوز لتجنب أي تعارض في النصوص
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import database

# إعداد السجلات (Logging)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# تحميل المتغيرات من .env
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
ORDERS_GROUP_ID = os.getenv("ORDERS_GROUP_ID")


def save_group_id_to_env(group_id: str) -> None:
    """حفظ معرف المجموعة في ملف .env ليظل ثابتاً بعد إعادة التشغيل"""
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_file):
        return
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        found = False
        new_lines = []
        for line in lines:
            if line.startswith("ORDERS_GROUP_ID="):
                new_lines.append(f"ORDERS_GROUP_ID={group_id}\n")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"ORDERS_GROUP_ID={group_id}\n")
        with open(env_file, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception as e:
        logger.warning(f"تعذر حفظ معرف المجموعة في .env: {e}")


# سيرفر فحص الصحة للاستضافات السحابية المجانية (Render, Koyeb, إلخ)
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Spark AI Telegram Bot is Running 24/7!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def start_health_check_server() -> None:
    """تشغيل خادم ويب خفيف في الخلفية لضمان عمل البوت على السيرفرات السحابية دون إغلاقه"""
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"سيرفر فحص الصحة السحابي يعمل على المنفذ {port}")
    except Exception as e:
        logger.warning(f"تعذر تشغيل سيرفر فحص الصحة على المنفذ {port}: {e}")



# حالات محادثة أخذ طلب حملة جديدة (Order Conversation States)
(
    STATE_SERVICE,
    STATE_BUSINESS,
    STATE_PLATFORMS,
    STATE_BUDGET,
    STATE_CONTACT,
    STATE_CONFIRM,
) = range(6)

# حالات متابعة الطلب (Track Conversation States)
STATE_TRACK_INPUT = 10


# دالة مساعدة لتعديل الرسائل بأمان ودون توقف في حال تكرار الضغط
async def safe_edit_message(
    query,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: str = "Markdown",
) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        if "Message is not modified" in str(e):
            pass
        else:
            logger.warning(f"تعذر تعديل الرسالة، جاري إرسال رد بديل: {e}")
            try:
                await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception:
                pass


# -------------------------------------------------------------
# 1. لوحات الأزرار والقوائم التفاعلية (Keyboards)
# -------------------------------------------------------------

def main_menu_keyboard() -> InlineKeyboardMarkup:
    """لوحة التحكم والقائمة الرئيسية لبوت Spark AI"""
    keyboard = [
        [
            InlineKeyboardButton("🚀 طلب حملة إعلانية جديدة", callback_data="menu_new_order"),
        ],
        [
            InlineKeyboardButton("📊 خدماتنا وباقات الإعلانات", callback_data="menu_services"),
            InlineKeyboardButton("💡 توصية المنصات الأنسب لك", callback_data="menu_recommend"),
        ],
        [
            InlineKeyboardButton("❓ الأسئلة الشائعة (FAQ)", callback_data="menu_faq"),
            InlineKeyboardButton("🔍 متابعة حالة طلبي", callback_data="menu_track"),
        ],
        [
            InlineKeyboardButton("👤 حسابي", callback_data="menu_my_info"),
            InlineKeyboardButton("📞 تحدث مع خبير تسويق", callback_data="menu_contact"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def back_to_main_keyboard() -> InlineKeyboardMarkup:
    """زر الرجوع الموحد للقائمة الرئيسية"""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="menu_main")]]
    )


def services_list_keyboard() -> InlineKeyboardMarkup:
    """قائمة استعراض الخدمات الإعلانية"""
    keyboard = [
        [InlineKeyboardButton("🎯 إعلانات فيسبوك وإنستغرام (Meta)", callback_data="srv_meta")],
        [InlineKeyboardButton("📱 إعلانات تيك توك وسناب شات", callback_data="srv_tiktok_snap")],
        [InlineKeyboardButton("🔍 إعلانات جوجل واليوتيوب (Google Ads)", callback_data="srv_google")],
        [InlineKeyboardButton("🚀 باقة النمو الشاملة (Full Growth)", callback_data="srv_full")],
        [InlineKeyboardButton("✍️ صناعة الفيديوهات والتصاميم الإعلانية", callback_data="srv_creatives")],
        [InlineKeyboardButton("⚡ أتمتة الردود والمبيعات بالذكاء الاصطناعي", callback_data="srv_auto")],
        [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def service_selection_keyboard() -> InlineKeyboardMarkup:
    """أزرار اختيار نوع الخدمة عند بدء طلب جديد"""
    keyboard = [
        [InlineKeyboardButton("🎯 إعلانات ميتا (Instagram & Facebook)", callback_data="sel_meta")],
        [InlineKeyboardButton("📱 إعلانات TikTok & Snapchat", callback_data="sel_tiktok_snap")],
        [InlineKeyboardButton("🔍 إعلانات Google & YouTube", callback_data="sel_google")],
        [InlineKeyboardButton("🚀 باقة تسويق رقمي متكاملة", callback_data="sel_full")],
        [InlineKeyboardButton("✍️ محتوى وتصاميم إعلانية بياعة", callback_data="sel_creatives")],
        [InlineKeyboardButton("⚡ أتمتة مبيعات وشات بوت AI", callback_data="sel_auto")],
        [InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_order")],
    ]
    return InlineKeyboardMarkup(keyboard)


def platforms_selection_keyboard() -> InlineKeyboardMarkup:
    """أزرار تحديد المنصات الإعلانية المستهدفة"""
    keyboard = [
        [InlineKeyboardButton("إنستغرام وفيسبوك (Meta)", callback_data="plt_meta")],
        [InlineKeyboardButton("تيك توك (TikTok Ads)", callback_data="plt_tiktok")],
        [InlineKeyboardButton("سناب شات (Snapchat Ads)", callback_data="plt_snap")],
        [InlineKeyboardButton("بحث جوجل ويوتيوب (Google Ads)", callback_data="plt_google")],
        [InlineKeyboardButton("🌟 جميع المنصات المناسبة (توصية الفريق)", callback_data="plt_all")],
        [InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_order")],
    ]
    return InlineKeyboardMarkup(keyboard)


def faq_menu_keyboard() -> InlineKeyboardMarkup:
    """قائمة الأسئلة الشائعة"""
    keyboard = [
        [InlineKeyboardButton("💰 كم أقل ميزانية إعلانية مقترحة؟", callback_data="faq_budget")],
        [InlineKeyboardButton("⏱️ متى تبدأ النتائج والمبيعات؟", callback_data="faq_time")],
        [InlineKeyboardButton("📈 كيف تضمنون أعلى عائد إعلاني (ROAS)؟", callback_data="faq_roas")],
        [InlineKeyboardButton("📊 هل أحصل على تقارير دورية لأداء الحملات؟", callback_data="faq_reports")],
        [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def recommend_quiz_keyboard() -> InlineKeyboardMarkup:
    """اختيار مجال النشاط لاقتراح أفضل المنصات الإعلانية"""
    keyboard = [
        [InlineKeyboardButton("👗 متجر إلكتروني / منتجات استهلاكية", callback_data="rec_ecommerce")],
        [InlineKeyboardButton("🏥 عيادة طبية / مركز تجميل أو أسنان", callback_data="rec_clinic")],
        [InlineKeyboardButton("🏢 خدمات شركات و B2B / استشارات", callback_data="rec_b2b")],
        [InlineKeyboardButton("🏡 عقارات واستثمار ومقاولات", callback_data="rec_realestate")],
        [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


# -------------------------------------------------------------
# 2. معالجات الأوامر الأساسية (Basic Commands)
# -------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """رسالة الترحيب الاحترافية لشركة Spark AI وإعادة تهيئة الحالة"""
    context.user_data.clear()
    user = update.effective_user

    text = (
        f"أهلاً بك يا **{user.first_name}** في **Spark AI**! 🚀🔥\n"
        "**وكالتك الرائدة للتسويق الرقمي والإعلانات الممولة المدعومة بالذكاء الاصطناعي.**\n\n"
        "نساعدك على مضاعفة مبيعاتك، تقليل تكلفة الاكتساب (CPA)، وتحقيق أعلى عائد إعلاني (ROAS) "
        "عبر إعلانات استراتيجية ومحتوى بياع وأتمتة مسارات التحويل.\n\n"
        "👇 **كيف يمكننا مساعدتك اليوم؟ اختر من القائمة أدناه:**"
    )
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
        await safe_edit_message(update.callback_query, text, reply_markup=main_menu_keyboard())
    else:
        await update.message.reply_text(
            text,
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دليل استخدام البوت والأوامر السريعة"""
    text = (
        "📖 **دليل استخدام بوت Spark AI للتسويق والإعلانات:**\n\n"
        "• `/start` - فتح القائمة الرئيسية وبدء طلب حملة جديدة\n"
        "• `/order` - طلب حملة إعلانية فوراً خطوة بخطوة\n"
        "• `/track <كود_الطلب>` - الاستعلام عن حالة حملتك الإعلانية\n"
        "• `/services` - استعراض تفاصيل وباقات خدماتنا\n"
        "• `/contact` - التواصل المباشر مع استشاري التسويق\n"
        "• `/admin` - لوحة تحكم الإدارة لمتابعة الطلبات (للمسؤولين فقط)\n"
        "• `/setgroup` - ربط مجموعة تيلجرام لتحويل الطلبات والإشعارات إليها فوراً\n\n"
        "💡 *يمكنك أيضاً الكتابة مباشرة للبوت بأي سؤال حول الأسعار أو المنصات وسيقوم بالرد الفوري عليك!*"
    )
    await update.message.reply_text(
        text,
        reply_markup=back_to_main_keyboard(),
        parse_mode="Markdown",
    )


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """عرض بيانات حساب المستخدم التقنية ومعرفه"""
    user = update.effective_user
    chat = update.effective_chat
    text = (
        "👤 **معلومات حسابك التقنية:**\n\n"
        f"• **الاسم:** {user.full_name}\n"
        f"• **معرف تيلجرام (User ID):** `{user.id}`\n"
        f"• **اسم المستخدم:** @{user.username if user.username else 'غير محدد'}\n"
        f"• **معرف المحادثة (Chat ID):** `{chat.id}`\n\n"
        "💡 *استخدم هذا المعرف في إعدادات الأدمن إذا كنت مسؤولاً في الشركة.*"
    )
    await update.message.reply_text(
        text,
        reply_markup=back_to_main_keyboard(),
        parse_mode="Markdown",
    )


# -------------------------------------------------------------
# 3. مسار أخذ طلبات الحملات الإعلانية (Conversation Handler)
# -------------------------------------------------------------

async def start_new_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بدء مسار طلب حملة جديدة باختيار الخدمة"""
    context.user_data.clear()
    text = (
        "🚀 **بدء طلب حملة إعلانية جديدة مع Spark AI:**\n\n"
        "الخطوة (1 من 5): **ما هي الخدمة الأساسية التي ترغب بها؟**\n"
        "اختر من الأزرار أدناه:"
    )
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
        await safe_edit_message(
            update.callback_query,
            text,
            reply_markup=service_selection_keyboard(),
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=service_selection_keyboard(),
            parse_mode="Markdown",
        )
    return STATE_SERVICE


async def select_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار الخدمة والانتقال لطلب اسم النشاط والمجال"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    data = query.data

    if data == "cancel_order":
        await safe_edit_message(
            query,
            "❌ تم إلغاء طلب الحملة. يمكنك العودة والطلب في أي وقت.",
            reply_markup=back_to_main_keyboard(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    service_names = {
        "sel_meta": "إعلانات ميتا (Instagram & Facebook)",
        "sel_tiktok_snap": "إعلانات تيك توك وسناب شات",
        "sel_google": "إعلانات جوجل واليوتيوب (Google Ads)",
        "sel_full": "باقة تسويق رقمي متكاملة (360°)",
        "sel_creatives": "صناعة محتوى وتصاميم وفيديوهات إعلانية",
        "sel_auto": "أتمتة مبيعات وشات بوت AI",
    }
    chosen_service = service_names.get(data, "إعلانات ممولة")
    context.user_data["service_type"] = chosen_service

    text = (
        f"✅ تم اختيار: **{chosen_service}**\n\n"
        "الخطوة (2 من 5): **ما هو اسم نشاطك التجاري / متجرك ومجال عملك؟**\n"
        "*(مثال: متجر لورانس للأزياء، عيادات النخبة للأسنان، تطبيق توصيل...)*\n\n"
        "✍️ **أرسل اسم ومجال النشاط في رسالة نصية الآن:**"
    )
    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_order")]]
    )
    await safe_edit_message(query, text, reply_markup=cancel_kb)
    return STATE_BUSINESS


async def enter_business_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """استلام اسم ومجال النشاط التجاري والانتقال لاختيار المنصات"""
    business_name = update.message.text.strip()
    if len(business_name) < 2:
        await update.message.reply_text("⚠️ يرجى كتابة اسم صحيح لنشاطك التجاري للمتابعة:")
        return STATE_BUSINESS

    context.user_data["business_name"] = business_name

    text = (
        f"👍 ممتاز! تم تسجيل النشاط: **{business_name}**\n\n"
        "الخطوة (3 من 5): **ما هي المنصات الإعلانية التي تفضل إطلاق الحملات عليها؟**\n"
        "اختر من الأزرار أو اختر توصية الفريق:"
    )
    await update.message.reply_text(
        text,
        reply_markup=platforms_selection_keyboard(),
        parse_mode="Markdown",
    )
    return STATE_PLATFORMS


async def select_platform_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار المنصات والانتقال لتحديد الميزانية والهدف"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    data = query.data

    if data == "cancel_order":
        await safe_edit_message(
            query,
            "❌ تم إلغاء طلب الحملة.",
            reply_markup=back_to_main_keyboard(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    platform_names = {
        "plt_meta": "إنستغرام وفيسبوك (Meta)",
        "plt_tiktok": "تيك توك (TikTok Ads)",
        "plt_snap": "سناب شات (Snapchat Ads)",
        "plt_google": "بحث جوجل ويوتيوب (Google Ads)",
        "plt_all": "جميع المنصات الموصى بها حسب دراسة النشاط",
    }
    chosen_plt = platform_names.get(data, "توصية الفريق")
    context.user_data["platforms"] = chosen_plt

    text = (
        f"🎯 المنصات المختارة: **{chosen_plt}**\n\n"
        "الخطوة (4 من 5): **كم الميزانية الإعلانية التقريبية وما هو هدفك الرئيسي؟**\n"
        "*(مثال: 500$ شهرياً بهدف زيادة المبيعات على المتجر، أو 1000$ لجلب عملاء محتملين للعيادة)*\n\n"
        "✍️ **اكتب الميزانية والهدف في رسالة نصية:**"
    )
    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_order")]]
    )
    await safe_edit_message(query, text, reply_markup=cancel_kb)
    return STATE_BUDGET


async def enter_budget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """استلام الميزانية والهدف والانتقال لطلب بيانات التواصل"""
    budget_goal = update.message.text.strip()
    context.user_data["budget_goal"] = budget_goal

    text = (
        "الخطوة (5 من 5): **بيانات التواصل مع مسؤول الحملة** 📞\n\n"
        "يرجى إرسال **اسمك الكريم** مع **رقم الواتساب أو الهاتف** (مع مفتاح الدولة):\n"
        "*(مثال: محمد الأحمد - +966501234567)*\n\n"
        "✍️ **أرسل اسمك ورقم هاتفك الآن في رسالة:**"
    )
    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_order")]]
    )
    await update.message.reply_text(text, reply_markup=cancel_kb, parse_mode="Markdown")
    return STATE_CONTACT


async def enter_contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """استلام بيانات التواصل وعرض ملخص الطلب للتأكيد النهائي"""
    contact_raw = update.message.text.strip()
    context.user_data["contact_raw"] = contact_raw
    context.user_data["client_name"] = update.effective_user.full_name
    context.user_data["contact_phone"] = contact_raw

    summary_text = (
        "📋 **ملخص طلب الحملة الإعلانية لدى Spark AI:**\n"
        "──────────────────────\n"
        f"🔹 **الخدمة:** {context.user_data.get('service_type')}\n"
        f"🔹 **النشاط التجاري:** {context.user_data.get('business_name')}\n"
        f"🔹 **المنصات المستهدفة:** {context.user_data.get('platforms')}\n"
        f"🔹 **الميزانية والهدف:** {context.user_data.get('budget_goal')}\n"
        f"🔹 **بيانات التواصل:** {context.user_data.get('contact_phone')}\n"
        "──────────────────────\n"
        "هل تود تأكيد وإرسال هذا الطلب لفريق الإعلانات والتسويق؟"
    )

    confirm_keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ نعم، أرسل الطلب فوراً", callback_data="confirm_send_order")],
            [InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_order")],
        ]
    )
    await update.message.reply_text(summary_text, reply_markup=confirm_keyboard, parse_mode="Markdown")
    return STATE_CONFIRM


async def confirm_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """حفظ الطلب في قاعدة البيانات، وإرسال التنبيهات وإصدار الكود للعميل"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    data = query.data

    if data == "cancel_order":
        await safe_edit_message(
            query,
            "❌ تم إلغاء الطلب. شكراً لاهتمامك بـ Spark AI.",
            reply_markup=back_to_main_keyboard(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    user = update.effective_user
    service_type = context.user_data.get("service_type", "إعلانات ممولة")
    business_name = context.user_data.get("business_name", "غير محدد")
    platforms = context.user_data.get("platforms", "توصية الفريق")
    budget_goal = context.user_data.get("budget_goal", "غير محدد")
    contact_phone = context.user_data.get("contact_phone", "غير محدد")
    client_name = user.full_name

    # 1. حفظ في قاعدة بيانات SQLite
    order_code = database.save_order(
        user_id=user.id,
        user_name=user.username or user.first_name,
        client_name=client_name,
        business_name=business_name,
        service_type=service_type,
        platforms=platforms,
        budget_goal=budget_goal,
        contact_phone=contact_phone,
    )

    # 2. رسالة النجاح للعميل
    success_text = (
        "🎉 **تم استلام طلبك بنجاح في Spark AI!**\n\n"
        f"🏷️ **كود متابعة الطلب الخاص بك:** `{order_code}`\n"
        "*(يرجى الاحتفاظ بهذا الكود لمتابعة حالة الحملة في أي وقت)*\n\n"
        "⚡ **ماذا سيحدث الآن؟**\n"
        "1. يقوم خبراؤنا بدراسة نشاطك ومنافسيك وتجهيز الخطة الإعلانية.\n"
        "2. سيتواصل معك مدير الحملات عبر الواتساب أو الهاتف خلال ساعات قليلة.\n\n"
        "شكراً لثقتك في Spark AI 🚀"
    )
    await safe_edit_message(
        query,
        success_text,
        reply_markup=back_to_main_keyboard(),
    )

    # 3. إرسال تنبيه تفاعلي فوري للمجموعة / الأدمن
    alert_text = (
        "🚨 **طلب حملة إعلانية جديد وصل للتو!** 🚨\n"
        "──────────────────────\n"
        f"🏷️ **كود الطلب:** `{order_code}`\n"
        f"👤 **العميل:** {client_name} " + (f"(@{user.username})" if user.username else "") + "\n"
        f"🏢 **النشاط:** {business_name}\n"
        f"🎯 **الخدمة:** {service_type}\n"
        f"📱 **المنصات:** {platforms}\n"
        f"💵 **الميزانية والهدف:** {budget_goal}\n"
        f"📞 **رقم التواصل:** `{contact_phone}`\n"
        f"🆔 **معرف تيلجرام:** `{user.id}`\n"
        "──────────────────────\n"
        "🔄 **الحالة:** 🟡 جديد - قيد المراجعة"
    )

    # تجهيز أزرار التواصل المباشر وإجراءات فريق العمل
    clean_digits = "".join(ch for ch in contact_phone if ch.isdigit())
    contact_btns = []
    if clean_digits:
        contact_btns.append(InlineKeyboardButton("💬 مراسلة واتساب", url=f"https://wa.me/{clean_digits}"))
    if user.username:
        contact_btns.append(InlineKeyboardButton("✈️ حساب تيلجرام", url=f"https://t.me/{user.username}"))

    action_btns = [
        InlineKeyboardButton("✅ استلام والتواصل", callback_data=f"grp_take_{order_code}"),
        InlineKeyboardButton("🚀 إطلاق الحملة", callback_data=f"grp_launch_{order_code}"),
    ]

    group_kb = []
    if contact_btns:
        group_kb.append(contact_btns)
    group_kb.append(action_btns)
    group_markup = InlineKeyboardMarkup(group_kb)

    # الإرسال للمجموعة (ORDERS_GROUP_ID) وللأدمن (ADMIN_CHAT_ID)
    destinations = set()
    if ORDERS_GROUP_ID and ORDERS_GROUP_ID.strip():
        destinations.add(ORDERS_GROUP_ID.strip())
    if ADMIN_CHAT_ID and ADMIN_CHAT_ID.strip():
        destinations.add(ADMIN_CHAT_ID.strip())

    for dest_id in destinations:
        try:
            await context.bot.send_message(
                chat_id=int(dest_id),
                text=alert_text,
                reply_markup=group_markup,
                parse_mode="Markdown",
            )
            logger.info(f"تم إرسال إشعار الطلب {order_code} بنجاح إلى: {dest_id}")
        except Exception as e:
            logger.warning(f"تعذر إرسال الإشعار إلى {dest_id}: {e}")

    logger.info(f"تم تسجيل طلب جديد بنجاح: {order_code} من المستخدم {user.id}")
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """إلغاء مسار المحادثة في أي وقت عبر أمر /cancel أو زر الإلغاء"""
    context.user_data.clear()
    text = "❌ تم إلغاء العملية والعودة للقائمة الرئيسية."
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
        await safe_edit_message(update.callback_query, text, reply_markup=back_to_main_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=back_to_main_keyboard(), parse_mode="Markdown")
    return ConversationHandler.END


# -------------------------------------------------------------
# 4. مسار متابعة حالة الطلب (Order Tracking)
# -------------------------------------------------------------

async def start_track_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """طلب كود المتابعة من العميل"""
    text = (
        "🔍 **متابعة حالة حملتك الإعلانية:**\n\n"
        "يرجى كتابة وإرسال **كود الطلب** الخاص بك (مثل: `SPARK-MKT-1234`):\n\n"
        "*(أو أرسل /cancel للرجوع)*"
    )
    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="menu_main")]]
    )
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
        await safe_edit_message(update.callback_query, text, reply_markup=cancel_kb)
    else:
        await update.message.reply_text(text, reply_markup=cancel_kb, parse_mode="Markdown")
    return STATE_TRACK_INPUT


async def process_track_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """البحث عن كود الطلب وإرجاع حالته للعميل"""
    code_input = update.message.text.strip().upper()
    order = database.get_order_by_code(code_input)

    if not order:
        text = (
            f"❌ عذراً، لم يتم العثور على أي طلب بالكود: `{code_input}`\n\n"
            "يرجى التأكد من كتابة الكود بشكل صحيح وإعادة المحاولة، أو التواصل مع الدعم."
        )
        retry_kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔄 المحاولة مجدداً", callback_data="menu_track")],
                [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="menu_main")],
            ]
        )
        await update.message.reply_text(text, reply_markup=retry_kb, parse_mode="Markdown")
        return ConversationHandler.END

    status_text = (
        f"📊 **تفاصيل وحالة الطلب: `{order['order_code']}`**\n"
        "──────────────────────\n"
        f"🏷️ **النشاط:** {order['business_name']}\n"
        f"🎯 **نوع الخدمة:** {order['service_type']}\n"
        f"📱 **المنصات:** {order['platforms']}\n"
        f"📅 **تاريخ الطلب:** {order['created_at']}\n"
        f"🔄 **الحالة الحالية:** 🟢 **{order['status']}**\n"
        "──────────────────────\n"
        "إذا كان لديك أي تعديل أو استفسار بخصوص حملتك، تواصل مع فريقنا مباشرة."
    )
    await update.message.reply_text(
        status_text,
        reply_markup=back_to_main_keyboard(),
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def track_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """أمر /track المباشر مع كود الطلب (مثل: /track SPARK-MKT-1234)"""
    if not context.args:
        await update.message.reply_text(
            "⚠️ الرجاء كتابة كود الطلب بعد الأمر، مثال:\n`/track SPARK-MKT-1234`",
            parse_mode="Markdown",
        )
        return

    order_code = context.args[0].strip().upper()
    order = database.get_order_by_code(order_code)

    if not order:
        await update.message.reply_text(
            f"❌ لم يتم العثور على طلب برقم `{order_code}`. تأكد من صحة الرمز.",
            parse_mode="Markdown",
        )
        return

    status_text = (
        f"📊 **تفاصيل الطلب: `{order['order_code']}`**\n"
        f"• **النشاط:** {order['business_name']}\n"
        f"• **الخدمة:** {order['service_type']}\n"
        f"• **الحالة:** 🟢 **{order['status']}**\n"
        f"• **التاريخ:** {order['created_at']}"
    )
    await update.message.reply_text(status_text, reply_markup=back_to_main_keyboard(), parse_mode="Markdown")


# -------------------------------------------------------------
# 5. معالجات القوائم الفرعية واستعراض الخدمات (Menu Callbacks)
# -------------------------------------------------------------

async def menu_callbacks_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """الاستجابة لضغطات الأزرار العامة والقوائم الفرعية مع إنهاء المحادثات بأمان"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    data = query.data
    logger.info(f"ضغط زر: {data} من المستخدم {update.effective_user.id}")

    # زر بدء طلب جديد
    if data == "menu_new_order":
        return await start_new_order(update, context)

    # زر متابعة الطلب
    elif data == "menu_track":
        return await start_track_order(update, context)

    # زر القائمة الرئيسية
    elif data == "menu_main":
        user = update.effective_user
        text = (
            f"أهلاً بك مجدداً يا **{user.first_name}** في **Spark AI**! 🚀🔥\n\n"
            "اختر الإجراء المطلوب من القائمة الرئيسية:"
        )
        await safe_edit_message(
            query,
            text,
            reply_markup=main_menu_keyboard(),
        )

    elif data == "menu_services":
        text = (
            "📊 **باقات وخدمات Spark AI للتسويق الرقمي والإعلانات:**\n\n"
            "اضغط على أي خدمة أدناه للاطلاع على تفاصيلها الكاملة ومزاياها وكيف نضاعف مبيعاتك من خلالها:"
        )
        await safe_edit_message(
            query,
            text,
            reply_markup=services_list_keyboard(),
        )

    # تفاصيل الخدمات الفردية
    elif data == "srv_meta":
        text = (
            "🎯 **إعلانات فيسبوك وإنستغرام (Meta Ads):**\n\n"
            "• استهداف دقيق ومخصص للجمهور الأكثر استعداداً للشراء.\n"
            "• إعلانات ريلز (Reels) وكاروسيل جذابة ومحسنة للموبايل.\n"
            "• إعداد وتتبع احترافي لـ Meta Pixel و Conversions API لضمان تتبع كل عملية بيع بدقة.\n"
            "• إعادة استهداف الزوار (Retargeting) لاستعادة السلات المتروكة.\n\n"
            "🔥 *مثالية للمتاجر الإلكترونية، العيادات، والعلامات التجارية المحلية.*"
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 اطلب هذه الحملة الآن", callback_data="menu_new_order")],
                [InlineKeyboardButton("🔙 عودة للخدمات", callback_data="menu_services")],
            ]
        )
        await safe_edit_message(query, text, reply_markup=kb)

    elif data == "srv_tiktok_snap":
        text = (
            "📱 **إعلانات تيك توك وسناب شات (TikTok & Snapchat Ads):**\n\n"
            "• الوصول للفئات الشبابية والأكثر تفاعلاً ونشاطاً شرائياً.\n"
            "• فيديوهات إعلانية بأسلوب المحتوى العفوي (UGC) الذي يحقق أعلى تحويل ومبيعات.\n"
            "• استهداف جغرافي وديموغرافي دقيق.\n"
            "• تكلفة نقرة وظهور منخفضة مقارنة بالمنصات الأخرى.\n\n"
            "🔥 *الأكثر فعالية في أسواق الخليج والوطن العربي للمنتجات السريعة والمطاعم والأزياء.*"
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 اطلب هذه الحملة الآن", callback_data="menu_new_order")],
                [InlineKeyboardButton("🔙 عودة للخدمات", callback_data="menu_services")],
            ]
        )
        await safe_edit_message(query, text, reply_markup=kb)

    elif data == "srv_google":
        text = (
            "🔍 **إعلانات شبكة بحث جوجل واليوتيوب (Google & YouTube Ads):**\n\n"
            "• الاستحواذ على العميل وهو يبحث بنفسه عن خدمتك أو منتجك (High Intent).\n"
            "• حملات الأداء الأقصى (Performance Max) للظهور عبر جميع منتجات جوجل في وقت واحد.\n"
            "• إعلانات يوتيوب الترويجية لزيادة الوعي والمصداقية.\n\n"
            "🔥 *الخيار الأول للخدمات الاستشارية، العقارات، العيادات، والشركات.*"
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 اطلب هذه الحملة الآن", callback_data="menu_new_order")],
                [InlineKeyboardButton("🔙 عودة للخدمات", callback_data="menu_services")],
            ]
        )
        await safe_edit_message(query, text, reply_markup=kb)

    elif data == "srv_full":
        text = (
            "🚀 **باقة النمو الشاملة (Full Growth 360°):**\n\n"
            "باقة متكاملة تدير فيها Spark AI منظومتك التسويقية بالكامل:\n"
            "1. إطلاق وإدارة الحملات عبر جميع المنصات الأنسب لك.\n"
            "2. كتابة وتصميم كافة الإعلانات والفيديوهات الترويجية شهرياً.\n"
            "3. بناء صفحات هبوط سريعة ومحسنة لمعدل التحويل (High-Converting Landing Pages).\n"
            "4. أتمتة الردود والمبيعات عبر الشات بوت والواتساب.\n"
            "5. تقارير أسبوعية وتحليل متقدم للأرباح والعائد (ROAS)."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 اطلب الباقة الشاملة الآن", callback_data="menu_new_order")],
                [InlineKeyboardButton("🔙 عودة للخدمات", callback_data="menu_services")],
            ]
        )
        await safe_edit_message(query, text, reply_markup=kb)

    elif data == "srv_creatives":
        text = (
            "✍️ **صناعة المحتوى والتصاميم والفيديوهات الإعلانية:**\n\n"
            "• كتابة نصوص إعلانية بياعة (Direct-Response Copywriting) تركز على حل مشاكل العميل وإبراز القيمة.\n"
            "• تصاميم إعلانية بصرية احترافية متوافقة مع معايير المنصات.\n"
            "• مونتاج فيديوهات إعلانية سريعة وقوية (Hook - Story - Offer).\n\n"
            "🔥 *الإعلان المميز هو السر في خفض تكلفة الحملة بنسبة تتجاوز 40%!*"
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 اطلب محتوى وتصاميم الآن", callback_data="menu_new_order")],
                [InlineKeyboardButton("🔙 عودة للخدمات", callback_data="menu_services")],
            ]
        )
        await safe_edit_message(query, text, reply_markup=kb)

    elif data == "srv_auto":
        text = (
            "⚡ **أتمتة المبيعات والردود بالذكاء الاصطناعي:**\n\n"
            "• ربط الإعلانات بشات بوت ذكي يرد فورياً على رسائل العملاء 24/7 دون أي تأخير.\n"
            "• فلترة وتأهيل العملاء المهتمين وجمع أرقامهم وبياناتهم تلقائياً.\n"
            "• التكامل مع الواتساب، وتيليجرام، وإنستغرام دايركت.\n"
            "• تحويل العميل من مجرد مشاهد للإعلان إلى مشترٍ فعلي في ثوانٍ!"
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 اطلب أتمتة المبيعات الآن", callback_data="menu_new_order")],
                [InlineKeyboardButton("🔙 عودة للخدمات", callback_data="menu_services")],
            ]
        )
        await safe_edit_message(query, text, reply_markup=kb)

    # توصية المنصات الإعلانية
    elif data == "menu_recommend":
        text = (
            "💡 **حاسبة ومستشار اختيار المنصات الأنسب لنشاطك:**\n\n"
            "اختر مجال نشاطك التجاري لنعطيك التوصية المثالية لأفضل المنصات وأقل ميزانية مقترحة لبدء نتائج قوية:"
        )
        await safe_edit_message(
            query,
            text,
            reply_markup=recommend_quiz_keyboard(),
        )

    elif data == "rec_ecommerce":
        text = (
            "👗 **توصية Spark AI للمتاجر الإلكترونية والمنتجات:**\n\n"
            "• **أفضل المنصات:** تيك توك + إنستغرام (مع سناب شات في دول الخليج).\n"
            "• **نوع الإعلانات الموصى به:** فيديوهات قصيرة للمنتج أثناء الاستخدام (UGC) مع عروض واضحة.\n"
            "• **الميزانية المبدئية المقترحة:** تبدأ من 300$ - 600$ شهرياً لاختبار المنتجات وبدء المبيعات.\n"
            "• **العائد المتوقع:** نسعى دائماً لتحقيق ROAS يتراوح بين 3x إلى 6x بعد مرحلة التحسين."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 اطلب حملة لمتجرك الآن", callback_data="menu_new_order")],
                [InlineKeyboardButton("🔙 العودة لاختيار مجال آخر", callback_data="menu_recommend")],
            ]
        )
        await safe_edit_message(query, text, reply_markup=kb)

    elif data == "rec_clinic":
        text = (
            "🏥 **توصية Spark AI للعيادات والمراكز الطبية والتجميل:**\n\n"
            "• **أفضل المنصات:** إعلانات بحث جوجل (للباحثين عن علاج فوري) + إنستغرام وسناب شات (لحالات قبل وبعد والتجميل).\n"
            "• **نوع الحملة:** حملات حجز مواعيد وتواصل مباشر عبر الواتساب (Click-to-WhatsApp).\n"
            "• **الميزانية المبدئية المقترحة:** 400$ - 800$ شهرياً حسب المنطقة والمنافسة."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 اطلب حملة لعيادتك الآن", callback_data="menu_new_order")],
                [InlineKeyboardButton("🔙 العودة لاختيار مجال آخر", callback_data="menu_recommend")],
            ]
        )
        await safe_edit_message(query, text, reply_markup=kb)

    elif data == "rec_b2b":
        text = (
            "🏢 **توصية Spark AI لشركات B2B والخدمات:**\n\n"
            "• **أفضل المنصات:** إعلانات شبكة بحث جوجل (Google Search) + لينكد إن وفيسبوك.\n"
            "• **نوع الحملة:** حملات جلب بيانات العملاء المحتملين والشركات (Lead Generation).\n"
            "• **الميزانية المبدئية المقترحة:** 500$ - 1000$ شهرياً."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 اطلب حملة لشركتك الآن", callback_data="menu_new_order")],
                [InlineKeyboardButton("🔙 العودة لاختيار مجال آخر", callback_data="menu_recommend")],
            ]
        )
        await safe_edit_message(query, text, reply_markup=kb)

    elif data == "rec_realestate":
        text = (
            "🏡 **توصية Spark AI للعقارات والمشاريع الكبرى:**\n\n"
            "• **أفضل المنصات:** إعلانات ميتا (Facebook Lead Forms) + جوجل وسناب شات.\n"
            "• **نوع الحملة:** تصوير وفيديو احترافي للمشروع مع فلترة العملاء الجادين عبر استبيان مخصص.\n"
            "• **الميزانية المبدئية المقترحة:** تبدأ من 700$ - 1500$ شهرياً لجلب اتصالات جادة ومباشرة."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 اطلب حملة لمشروعك الآن", callback_data="menu_new_order")],
                [InlineKeyboardButton("🔙 العودة لاختيار مجال آخر", callback_data="menu_recommend")],
            ]
        )
        await safe_edit_message(query, text, reply_markup=kb)

    # الأسئلة الشائعة FAQ
    elif data == "menu_faq":
        text = (
            "❓ **الأسئلة الشائعة حول خدمات وإعلانات Spark AI:**\n\n"
            "اختر السؤال لمعرفة الإجابة الفورية:"
        )
        await safe_edit_message(query, text, reply_markup=faq_menu_keyboard())

    elif data == "faq_budget":
        text = (
            "💰 **كم أقل ميزانية إعلانية مقترحة للبدء؟**\n\n"
            "تختلف الميزانية حسب مجالك ودولتك، ولكن عموماً ننصح بميزانية إعلانية لا تقل عن **300$ إلى 500$ شهرياً** "
            "لتغذية خوارزميات الذكاء الاصطناعي في المنصات بالبيانات الكافية للوصول لأفضل شريحة مشترية."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 اطلب استشارتك وحملتك", callback_data="menu_new_order")],
                [InlineKeyboardButton("🔙 عودة للأسئلة الشائعة", callback_data="menu_faq")],
            ]
        )
        await safe_edit_message(query, text, reply_markup=kb)

    elif data == "faq_time":
        text = (
            "⏱️ **متى تبدأ النتائج والمبيعات في الظهور؟**\n\n"
            "• **خلال أول 24-48 ساعة:** تبدأ الحملات في العمل واستقبال أولى التفاعلات والزيارات.\n"
            "• **خلال الأسبوع الأول:** نمر بمرحلة التعلم (Learning Phase) واختبار التصاميم والجمهور، وتبدأ المبيعات بالانتظام.\n"
            "• **من الأسبوع الثاني فصاعداً:** تبدأ مرحلة التوسع (Scaling) ومضاعفة الميزانية على الإعلانات الرابحة فقط."
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 عودة للأسئلة الشائعة", callback_data="menu_faq")]])
        await safe_edit_message(query, text, reply_markup=kb)

    elif data == "faq_roas":
        text = (
            "📈 **كيف تضمنون أعلى عائد على الإنفاق الإعلاني (ROAS)؟**\n\n"
            "نستخدم استراتيجية علمية قائمة على 4 ركائز:\n"
            "1. نصوص وتصاميم إعلانية تخاطب رغبة العميل بدقة.\n"
            "2. تحسين صفحات الهبوط ومسار الشراء لتقليل الانسحاب.\n"
            "3. اختبار مستمر (A/B Testing) للنصوص والجمهور والعروض.\n"
            "4. إيقاف الإعلانات الضعيفة فوراً وتركيز الميزانية على الإعلانات الأكثر مبيعاً."
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 عودة للأسئلة الشائعة", callback_data="menu_faq")]])
        await safe_edit_message(query, text, reply_markup=kb)

    elif data == "faq_reports":
        text = (
            "📊 **هل أحصل على تقارير تفصيلية لأداء الحملات؟**\n\n"
            "نعم بالتأكيد! ستحصل على:\n"
            "• لوحة تحكم وبيانات حية توضح المبيعات والنقرات وتكلفة الشراء.\n"
            "• تقارير دورية توضح العائد الإعلاني والتوصيات للتوسع في الشهر التالي.\n"
            "• شفافية تامة في وصولك لحساباتك الإعلانية."
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 عودة للأسئلة الشائعة", callback_data="menu_faq")]])
        await safe_edit_message(query, text, reply_markup=kb)

    # التواصل المباشر وحسابي
    elif data == "menu_contact":
        text = (
            "📞 **تواصل مباشر مع فريق واستشاري Spark AI:**\n\n"
            "يسعدنا دائماً الإجابة على استفساراتك ومناقشة تفاصيل مشروعك:\n\n"
            "• **المقر:** Spark AI - Digital Marketing & Growth\n"
            "• **الاستشارات السريعة:** يمكنك بدء طلب حملة وسيتواصل معك خبيرنا هاتفياً فوراً.\n"
            "• **عبر تيلجرام:** أرسل رسالتك هنا وسنرد عليك في أقرب وقت."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 اطلب حملتك الآن", callback_data="menu_new_order")],
                [InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="menu_main")],
            ]
        )
        await safe_edit_message(query, text, reply_markup=kb)

    elif data == "menu_my_info":
        user = update.effective_user
        orders = database.get_orders_by_user(user.id)
        orders_text = ""
        if orders:
            orders_text = "\n\n📦 **آخر طلباتك المسجلة:**\n"
            for o in orders:
                orders_text += f"• `{o['order_code']}` - {o['business_name']} ({o['status']})\n"
        else:
            orders_text = "\n\n*(ليس لديك أي طلبات سابقة مسجلة حتى الآن)*"

        info_text = (
            "👤 **بيانات حسابك في النظام:**\n\n"
            f"• **الاسم:** {user.full_name}\n"
            f"• **معرف تيلجرام:** `{user.id}`\n"
            f"• **اسم المستخدم:** @{user.username if user.username else 'غير متاح'}"
            f"{orders_text}"
        )
        await safe_edit_message(
            query,
            info_text,
            reply_markup=back_to_main_keyboard(),
        )

    # معالجات تفاعلية لأزرار المجموعة (أتمتة استلام الحملات)
    elif data.startswith("grp_take_"):
        order_code = data.replace("grp_take_", "")
        clicker = update.effective_user
        database.update_order_status(order_code, f"تم التواصل بواسطة {clicker.full_name}")

        orig_text = query.message.text
        lines = orig_text.split("\n")
        new_lines = []
        for line in lines:
            if "الحالة:" in line:
                new_lines.append(f"🔄 **الحالة:** 🟢 تم الاستلام والتواصل بواسطة {clicker.full_name} (@{clicker.username or 'بدون'})")
            else:
                new_lines.append(line)
        updated_text = "\n".join(new_lines)
        if "تم الاستلام" not in updated_text:
            updated_text += f"\n\n🟢 **المسؤول المستلم:** {clicker.full_name}"

        clean_digits = ""
        for line in lines:
            if "رقم التواصل:" in line:
                clean_digits = "".join(ch for ch in line if ch.isdigit())
                break

        new_kb = []
        if clean_digits:
            new_kb.append([InlineKeyboardButton("💬 مراسلة واتساب", url=f"https://wa.me/{clean_digits}")])
        new_kb.append([InlineKeyboardButton("🚀 تم إطلاق الحملة بنجاح", callback_data=f"grp_launch_{order_code}")])

        await safe_edit_message(
            query,
            updated_text,
            reply_markup=InlineKeyboardMarkup(new_kb),
        )

    elif data.startswith("grp_launch_"):
        order_code = data.replace("grp_launch_", "")
        clicker = update.effective_user
        database.update_order_status(order_code, "حملة نشطة تم إطلاقها 🚀")

        orig_text = query.message.text
        lines = orig_text.split("\n")
        new_lines = []
        for line in lines:
            if "الحالة:" in line:
                new_lines.append(f"🔄 **الحالة:** 🚀 **حملة نشطة تم إطلاقها** (بواسطة {clicker.full_name})")
            else:
                new_lines.append(line)
        updated_text = "\n".join(new_lines)
        await safe_edit_message(query, updated_text)

    else:
        logger.warning(f"زر غير معالج: {data}")

    return ConversationHandler.END


# -------------------------------------------------------------
# 6. ربط المجموعات وأوامر الإدارة (Group Link & Admin Panel)
# -------------------------------------------------------------

async def set_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """أمر /setgroup لربط مجموعة تيلجرام وتوجيه الطلبات إليها آلياً"""
    global ORDERS_GROUP_ID
    chat = update.effective_chat

    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text(
            "⚠️ هذا الأمر مخصص لمجموعات تيلجرام فقط!\n\n"
            "📌 **طريقة ربط المجموعة لاستقبال الطلبات:**\n"
            "1. أضف هذا البوت إلى مجموعتك أو مجموعة فريق العمل.\n"
            "2. تأكد من إعطاء البوت صلاحية إرسال الرسائل.\n"
            "3. اكتب الأمر `/setgroup` داخل المجموعة وسيقوم بالربط فوراً!"
        )
        return

    ORDERS_GROUP_ID = str(chat.id)
    save_group_id_to_env(ORDERS_GROUP_ID)

    text = (
        "🎉 **تم ربط هذه المجموعة بنجاح لاستقبال الطلبات!**\n\n"
        f"🏷️ **اسم المجموعة:** {chat.title}\n"
        f"🆔 **معرف المجموعة (Chat ID):** `{chat.id}`\n\n"
        "📢 سيقوم بوت **Spark AI** الآن بتحويل كل طلب حملة إعلانية ومعلومات العملاء الجديدة "
        "مباشرة إلى هذه المجموعة، مع أزرار للمراسلة السريعة على الواتساب وتحديث حالة الطلب."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def admin_orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """أمر /admin أو /orders لعرض آخر طلبات الحملات المسجلة"""
    orders = database.get_recent_orders(limit=10)

    if not orders:
        await update.message.reply_text("📭 لا توجد أي طلبات مسجلة في قاعدة البيانات حالياً.")
        return

    text = f"📋 **لوحة تحكم الطلبات - Spark AI (آخر {len(orders)} طلبات):**\n\n"
    for o in orders:
        text += (
            f"🏷️ **كود الطلب:** `{o['order_code']}`\n"
            f"👤 **العميل:** {o['client_name']} (@{o['user_name']})\n"
            f"🏢 **النشاط:** {o['business_name']} | 🎯 **الخدمة:** {o['service_type']}\n"
            f"📱 **المنصات:** {o['platforms']}\n"
            f"💵 **الميزانية والهدف:** {o['budget_goal']}\n"
            f"📞 **الهاتف:** `{o['contact_phone']}`\n"
            f"🔄 **الحالة:** {o['status']}\n"
            f"📅 **التاريخ:** {o['created_at']}\n"
            "──────────────────────\n"
        )

    await update.message.reply_text(text, parse_mode="Markdown")


# -------------------------------------------------------------
# 7. الردود التلقائية الذكية على الرسائل العادية (NLP & Auto-Replies)
# -------------------------------------------------------------

async def smart_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """الرد على الرسائل النصية العادية واستفسارات العملاء بذكاء"""
    msg = update.message.text.strip().lower()
    user_name = update.effective_user.first_name

    # تحيات
    if any(greet in msg for greet in ["السلام عليكم", "سلام", "سلام عليكم"]):
        response = (
            f"وعليكم السلام ورحمة الله وبركاته! أهلاً بك يا {user_name} في **Spark AI** 🌸\n"
            "يسعدنا مساعدتك في تنمية أعمالك ومضاعفة مبيعاتك عبر الإعلانات الممولة.\n"
            "اضغط على /start لفتح القائمة والخدمات."
        )
    elif any(greet in msg for greet in ["مرحبا", "مرحباً", "اهلا", "أهلا", "أهلاً", "هاي", "hello", "hi"]):
        response = (
            f"أهلاً وسهلاً بك يا {user_name}! كيف يمكن لـ **Spark AI** مساعدتك في حملاتك التسويقية اليوم؟ 😊\n"
            "استخدم /start لاختيار الخدمة أو طلب حملة جديدة."
        )
    elif any(kw in msg for kw in ["اسعار", "أسعار", "باقات", "سعر", "التكلفة", "تكلفة", "كم يكلف"]):
        response = (
            "💡 **أسعار وباقات الإعلانات لدى Spark AI:**\n\n"
            "تعتمد التكلفة على أهداف نشاطك التجاري والمنصات المختارة وحجم الميزانية الإعلانية.\n"
            "نوفر باقات مرنة تبدأ من إدارة الحملات المستقلة وصولاً إلى باقة النمو الشاملة (Full Growth).\n\n"
            "للحصول على تسعيرة دقيقة وخطة عمل تناسب ميزانيتك، يمكنك الضغط على /order لطلب حملة وسيتواصل معك خبيرنا مباشرة."
        )
    elif any(kw in msg for kw in ["اعلان", "إعلان", "اعلانات", "إعلانات", "حملة", "تسويق"]):
        response = (
            "🚀 **إدارة الحملات الإعلانية الممولة مع Spark AI:**\n\n"
            "ندير حملاتك على جميع المنصات: (Meta, TikTok, Snapchat, Google Ads).\n"
            "اضغط على /order لبدء تسجيل طلبك واختيار المنصة الأنسب لنشاطك."
        )
    elif any(kw in msg for kw in ["واتس", "واتساب", "تواصل", "رقم", "اتصال", "تلفون", "مكالمه"]):
        response = (
            "📞 **للتواصل مع فريق التسويق:**\n\n"
            "يمكنك تقديم طلبك عبر /order وسيقوم مدير الحملات بالتواصل معك عبر الواتساب فوراً، أو مراسلتنا هنا مباشرة."
        )
    elif any(kw in msg for kw in ["شكرا", "شكراً", "تسلم", "يعطيك العافيه", "مشكور"]):
        response = "العفو دائماً! نحن هنا في خدمتك لمساعدتك على النجاح دائماً 🌹"
    else:
        response = (
            f"وصلتنا رسالتك: \"{update.message.text}\" 👍\n\n"
            "إذا كنت ترغب في بدء حملة إعلانية جديدة، أو معرفة المنصات الأنسب لنشاطك، "
            "يرجى استخدام الأزرار في القائمة الرئيسية عبر الضغط على: /start"
        )

    await update.message.reply_text(response, parse_mode="Markdown")


# -------------------------------------------------------------
# 8. معالج الأخطاء العام
# -------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """تسجيل أي خطأ أثناء تشغيل البوت"""
    logger.error("حدث استثناء أثناء معالجة التحديث:", exc_info=context.error)


# -------------------------------------------------------------
# 9. نقطة الانطلاق الرئيسية (Main)
# -------------------------------------------------------------

def main() -> None:
    """تشغيل بوت Spark AI للتسويق الرقمي والإعلانات"""
    if not TOKEN or TOKEN == "your_bot_token_here":
        print("[ERROR] لم يتم تعيين TELEGRAM_BOT_TOKEN في ملف .env!")
        return

    print("[INFO] جاري تهيئة وتشغيل بوت Spark AI مع مهلة اتصال موسعة...")

    # تشغيل سيرفر فحص الصحة السحابي
    start_health_check_server()

    # زيادة مهلة الاتصال لمنع أخطاء ConnectTimeout و TimedOut
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    # بناء التطبيق
    application = (
        Application.builder()
        .token(TOKEN)
        .request(request)
        .build()
    )

    # 1. محادثة طلب حملة جديدة (Order Intake Conversation Handler)
    order_conv = ConversationHandler(
        entry_points=[
            CommandHandler("order", start_new_order),
            CallbackQueryHandler(start_new_order, pattern="^menu_new_order$"),
        ],
        states={
            STATE_SERVICE: [
                CallbackQueryHandler(select_service_callback, pattern="^(sel_.*|cancel_order)$"),
            ],
            STATE_BUSINESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_business_handler),
                CallbackQueryHandler(cancel_conversation, pattern="^cancel_order$"),
            ],
            STATE_PLATFORMS: [
                CallbackQueryHandler(select_platform_callback, pattern="^(plt_.*|cancel_order)$"),
            ],
            STATE_BUDGET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_budget_handler),
                CallbackQueryHandler(cancel_conversation, pattern="^cancel_order$"),
            ],
            STATE_CONTACT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_contact_handler),
                CallbackQueryHandler(cancel_conversation, pattern="^cancel_order$"),
            ],
            STATE_CONFIRM: [
                CallbackQueryHandler(confirm_order_callback, pattern="^(confirm_send_order|cancel_order)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conversation),
            CommandHandler("start", start_command),
            CommandHandler("order", start_new_order),
            CallbackQueryHandler(cancel_conversation, pattern="^cancel_order$"),
            # السماح بالخروج من المحادثة إلى أي زر من أزرار القوائم بسلاسة
            CallbackQueryHandler(menu_callbacks_handler, pattern="^menu_.*"),
            CallbackQueryHandler(menu_callbacks_handler, pattern="^(srv_.*|rec_.*|faq_.*)"),
        ],
        per_chat=True,
        per_user=True,
        per_message=False,
    )

    # 2. محادثة متابعة حالة الطلب (Order Tracking Conversation Handler)
    track_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_track_order, pattern="^menu_track$"),
        ],
        states={
            STATE_TRACK_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_track_input),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conversation),
            CommandHandler("start", start_command),
            CallbackQueryHandler(cancel_conversation, pattern="^(cancel_order|menu_main)$"),
            CallbackQueryHandler(menu_callbacks_handler, pattern="^menu_.*"),
        ],
        per_chat=True,
        per_user=True,
        per_message=False,
    )

    # تسجيل المحادثات أولاً
    application.add_handler(order_conv)
    application.add_handler(track_conv)

    # تسجيل الأوامر الأساسية
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("info", info_command))
    application.add_handler(CommandHandler("services", lambda u, c: menu_callbacks_handler(u, c)))
    application.add_handler(CommandHandler("track", track_command))
    application.add_handler(CommandHandler("admin", admin_orders_command))
    application.add_handler(CommandHandler("orders", admin_orders_command))
    application.add_handler(CommandHandler("setgroup", set_group_command))
    application.add_handler(CommandHandler("group", set_group_command))

    # تسجيل معالج القوائم والأزرار العامة
    application.add_handler(CallbackQueryHandler(menu_callbacks_handler))

    # تسجيل معالج النصوص العادية
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, smart_text_handler))

    # تسجيل معالج الأخطاء
    application.add_error_handler(error_handler)

    print("[SUCCESS] تم تشغيل بوت Spark AI بنجاح! البوت يستقبل الآن طلبات الحملات والإعلانات.")
    print("[INFO] رابط البوت على تيلجرام: https://t.me/Mohammad2008mx184BOT")

    # بدء استقبال الرسائل (Polling)
    application.run_polling()


if __name__ == "__main__":
    main()
