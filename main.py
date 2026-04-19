import os
from aiohttp import web
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramAPIError

from database import Database
from activation_manager import ActivationManager
from sms_client import GrizzlyClient, AliSMSClient, SMSAPIError

# ==========================================
# ⚙️ الإعدادات الأساسية (Configuration)
# ==========================================
API_TOKEN = "8033899165:AAHwx7_lIDxXLcPxyG0HqhQwg6FtY9u3TW8"
GRIZZLY_KEY = "0fee820164b18c68456a3f6197eb5900" # مفتاحك
ALI_KEY = "FM37hEbOKzTifWNjtEsLefhNzM8p9duuRyWRmoBvZSlgyJUGNv"

# إعداد الـ Logging باحترافية لتتبع الأخطاء بدقة
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()] # على الاستضافات المجانية يفضل StreamHandler لتجنب امتلاء المساحة
)
logger = logging.getLogger("ProductionBot")

# تهيئة المكونات (Components)
db = Database()
manager = ActivationManager(GRIZZLY_KEY, ALI_KEY, db)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ==========================================
# 🧠 إدارة الحالات (FSM States)
# ==========================================
class BotFlow(StatesGroup):
    waiting_for_provider = State()
    waiting_for_country_search = State()
    waiting_for_service_search = State()

# ==========================================
# 🏠 القائمة الرئيسية (Main Menu)
# ==========================================
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    await show_main_menu(message)

async def show_main_menu(message_or_callback):
    """عرض القائمة الرئيسية مع كافة الخيارات"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🛒 شراء رقم جديد", callback_data="menu_buy")
    builder.button(text="📋 التفعيلات النشطة", callback_data="menu_active")
    builder.button(text="💳 التحقق من الرصيد", callback_data="menu_balance")
    builder.adjust(1, 2)
    
    text = "🛠️ **نظام الصيد والتفعيلات الاحترافي**\n\nاختر العملية التي تود القيام بها:"
    
    if isinstance(message_or_callback, types.Message):
        await message_or_callback.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    else:
        await message_or_callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "menu_main")
async def back_to_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await show_main_menu(callback)

# ==========================================
# 💳 إدارة الرصيد (Balance)
# ==========================================
@dp.callback_query(F.data == "menu_balance")
async def check_balance(callback: types.CallbackQuery):
    await callback.message.edit_text("⏳ جاري التحقق من الرصيد في السيرفرات...")
    
    try:
        grizzly_bal = await manager.grizzly.get_balance()
    except Exception as e:
        grizzly_bal = f"خطأ: {e}"
        
    try:
        ali_bal = await manager.ali.get_balance()
    except Exception as e:
        ali_bal = f"خطأ: {e}"

    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 رجوع", callback_data="menu_main")
    
    text = (
        "💰 **تفاصيل الرصيد الحالي:**\n\n"
        f"🐻 **GrizzlySMS:** `{grizzly_bal}$`\n"
        f"🟠 **AliSMS:** `{ali_bal}$`"
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

# ==========================================
# 🛒 تدفق الشراء (Buy Flow)
# ==========================================
@dp.callback_query(F.data == "menu_buy")
async def choose_provider(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="🐻 Grizzly SMS", callback_data="prov_grizzly")
    builder.button(text="🟠 AliSMS", callback_data="prov_alisms")
    builder.button(text="🔙 رجوع", callback_data="menu_main")
    builder.adjust(2, 1)
    
    await callback.message.edit_text("اختر مزود الخدمة لبدء العمل:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("prov_"))
async def set_provider(callback: types.CallbackQuery, state: FSMContext):
    provider = callback.data.split("_")[1]
    await state.update_data(provider=provider)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🌍 البحث عن دولة", callback_data="btn_search_country")
    builder.button(text="🔙 رجوع للمزودين", callback_data="menu_buy")
    builder.adjust(1)
    
    await callback.message.edit_text(f"✅ تم اختيار: **{provider.upper()}**\nالآن، ابحث عن الدولة المطلوبة:", 
                                     reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "btn_search_country")
async def ask_country(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(BotFlow.waiting_for_country_search)
    await callback.message.edit_text("🔎 أرسل (اسم الدولة بالإنجليزية) أو (كود الدولة):\nمثال: `Egypt` أو `20`", parse_mode="Markdown")

@dp.message(BotFlow.waiting_for_country_search)
async def handle_country_search(message: types.Message, state: FSMContext):
    query = message.text.lower()
    await state.update_data(country_query=query)
    await state.set_state(BotFlow.waiting_for_service_search)
    await message.answer(f"✅ تم تحديد الدولة: `{query}`\nالآن أرسل اسم الخدمة المطلوبة (مثال: `whatsapp`, `telegram`):", parse_mode="Markdown")

@dp.message(BotFlow.waiting_for_service_search)
async def handle_service_search(message: types.Message, state: FSMContext):
    service_query = message.text.lower()
    data = await state.get_data()
    provider = data.get('provider')
    country_id = data.get('country_query')
    
    msg = await message.answer("⏳ جاري جلب المشغلين والأسعار الحية من السيرفر...")
    
    client = manager.grizzly if provider == "grizzly" else manager.ali
    service_map = {"whatsapp": "wa", "telegram": "tg", "facebook": "fb", "instagram": "ig", "google": "go"}
    service_id = service_map.get(service_query, service_query)

    try:
        operators = await client.get_operators(country_id, service_id) if hasattr(client, 'get_operators') else [{"id": "any", "name": "أسرع مشغل (تلقائي)", "price": "Auto", "count": "+10"}]
        
        builder = InlineKeyboardBuilder()
        for op in operators:
            btn_text = f"🎯 {op['name']} | {op['price']}$ ({op['count']})"
            builder.button(text=btn_text, callback_data=f"snipe_{service_id}_{country_id}_{op['id']}")
        
        builder.button(text="🔙 إلغاء", callback_data="menu_main")
        builder.adjust(1)
        
        await msg.edit_text(f"📦 **المشغلين المتاحين لـ {service_query.upper()}**\nاختر المشغل لبدء الصيد:", 
                             reply_markup=builder.as_markup(), parse_mode="Markdown")
                             
    except SMSAPIError as e:
        await msg.edit_text(f"❌ حدث خطأ أثناء الاتصال بالمزود: {str(e)}")

# ==========================================
# 🎯 إطلاق الصياد (Auto-Sniper)
# ==========================================
@dp.callback_query(F.data.startswith("snipe_"))
async def start_exclusive_snipe(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    service, country, operator = parts[1], parts[2], parts[3]
    user_id = str(callback.from_user.id)
    operator = None if operator == "any" else operator
    
    await callback.answer("🚀 بدأ الصيد الذكي...")
    
    await manager.start_sniper(user_id=user_id, service=service, country=country, operator=operator)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🛑 إيقاف الصيد", callback_data=f"stop_{user_id}_{service}_{country}")
    builder.button(text="📋 متابعة التفعيلات النشطة", callback_data="menu_active")
    builder.adjust(1)
    
    await callback.message.edit_text(
        f"🎯 **وضعية الصياد الآلي نشطة**\n\n"
        f"🔹 الخدمة: `{service}`\n"
        f"🔹 الدولة: `{country}`\n\n"
        "البوت يراقب السيرفر الآن.. سيصلك إشعار فور صيد الرقم واستلام الكود.",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("stop_"))
async def stop_sniper(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    user_id, service, country = parts[1], parts[2], parts[3]
    sniper_id = f"sniper_{user_id}_{service}_{country}"
    
    if sniper_id in manager.active_snipers:
        manager.active_snipers[sniper_id].cancel()
        manager.active_snipers.pop(sniper_id, None)
        await callback.message.edit_text("✅ تم إيقاف الصياد بنجاح.")
    else:
        await callback.answer("⚠️ لا يوجد صياد يعمل حالياً لهذا الطلب.", show_alert=True)

# ==========================================
# 📋 إدارة التفعيلات النشطة (Active Sessions)
# ==========================================
@dp.callback_query(F.data == "menu_active")
async def list_active_sessions(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    active_acts = await db.get_user_activations(user_id, status="WAITING") 
    
    if not active_acts:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 القائمة الرئيسية", callback_data="menu_main")
        await callback.message.edit_text("📭 لا يوجد لديك تفعيلات تنتظر الأكواد حالياً.", reply_markup=builder.as_markup())
        return

    text = "📋 **التفعيلات التي تنتظر الرسائل:**\n\n"
    builder = InlineKeyboardBuilder()
    
    for act in active_acts:
        act_id = act['activation_id']
        prov = act['provider']
        num = act['phone_number']
        
        text += f"📱 الرقم: `{num}` | ⚙️ {act['service']}\n"
        
        builder.button(text=f"❌ إلغاء", callback_data=f"act_cancel_{act_id}_{prov}")
        builder.button(text=f"🚫 حظر (Ban)", callback_data=f"act_ban_{act_id}_{prov}")
        builder.button(text=f"✅ إنهاء", callback_data=f"act_finish_{act_id}_{prov}")
        builder.button(text=f"🔄 كود جديد", callback_data=f"act_resend_{act_id}_{prov}")

    builder.adjust(4) 
    builder.row(types.InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="menu_main"))
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("act_"))
async def handle_activation_actions(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    action = parts[1]
    act_id = parts[2]
    provider = parts[3]
    
    await callback.answer("⏳ جاري تنفيذ الطلب...")
    
    try:
        if action == "cancel":
            success = await manager.cancel_number(act_id, provider)
            msg = "✅ تم إلغاء الرقم بنجاح واسترداد الرصيد." if success else "❌ فشل الإلغاء."
            
        elif action == "ban":
            success = await manager.ban_number(act_id, provider)
            msg = "🚫 تم حظر الرقم وإبلاغ المزود." if success else "❌ فشل حظر الرقم."
            
        elif action == "finish":
            success = await manager.finish_activation(act_id, provider)
            msg = "✅ تم إنهاء التفعيل وإغلاق الجلسة." if success else "❌ فشل إنهاء التفعيل."
            await db.update_status(act_id, "COMPLETED")
            
        elif action == "resend":
            client = manager.grizzly if provider == "grizzly" else manager.ali
            success = await client.set_status(act_id, 3)
            msg = "🔄 تم طلب إرسال كود جديد. يرجى الانتظار..." if success else "❌ فشل طلب كود جديد."
            
        await callback.message.edit_text(msg)
        await asyncio.sleep(2)
        await list_active_sessions(callback) 
        
    except SMSAPIError as e:
        await callback.answer(f"خطأ API: {str(e)}", show_alert=True)

# ==========================================
# 🔔 نظام الإشعارات الخلفي (Background Notifier)
# ==========================================
async def notification_worker():
    logger.info("Notification Worker started.")
    while True:
        try:
            unnotified = await db.get_unnotified_completed_activations() 
            
            for act in unnotified:
                user_id = act['user_id']
                num = act['phone_number']
                code = act['code']
                sms = act['sms_text']
                
                text = (
                    "🎉 **تم التقاط الكود بنجاح!**\n\n"
                    f"📱 **الرقم:** `{num}`\n"
                    f"🔑 **الكود:** `{code}`\n\n"
                    f"💬 **النص الكامل:**\n`{sms}`\n\n"
                    "💡 _لا تنسَ الضغط على 'إنهاء' من القائمة إذا لم تكن بحاجة لكود آخر._"
                )
                
                try:
                    await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
                    await db.mark_as_notified(act['activation_id']) 
                except TelegramAPIError as e:
                    logger.error(f"Failed to send notification to {user_id}: {e}")
                    
        except Exception as e:
            logger.error(f"Notification worker error: {e}")
            
        await asyncio.sleep(5) 

# ==========================================
# 🌐 خادم ويب وهمي للاستضافات المجانية
# ==========================================
async def handle_ping(request):
    return web.Response(text="Bot is alive and hunting!")

async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    # جلب البورت من بيئة الاستضافة أو استخدام 8080 كافتراضي
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"✅ Dummy web server started on port {port}")

# ==========================================
# 🏁 دورة حياة التشغيل (Application Lifecycle)
# ==========================================
async def on_startup():
    logger.info("🚀 Bot is starting...")
    await manager.restore_sessions()
    asyncio.create_task(notification_worker())

async def main():
    dp.startup.register(on_startup)
    
    # تشغيل خادم الويب الوهمي أولاً لتخطي فحص الاستضافة
    await start_dummy_server()
    
    try:
        # بدء عملية الـ Polling للبوت
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await manager.grizzly.close()
        await manager.ali.close()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped cleanly.")
