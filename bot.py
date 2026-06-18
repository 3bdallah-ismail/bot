import os
import logging
import threading
import time
import math
import csv
import requests
from io import StringIO
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load .env file if it exists locally
if os.path.exists(".env"):
    with open(".env", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

TOKEN = os.getenv("TOKEN")
SHEET_ID = os.getenv("SHEET_ID")

try:
    REFRESH_INTERVAL_MINUTES = int(os.getenv("REFRESH_INTERVAL_MINUTES", "5"))
except ValueError:
    REFRESH_INTERVAL_MINUTES = 5


REQUIRED_COLUMNS = [
    "الكود",
    "الباركود",
    "اسم",
    "الشركة",
    "سعر البيع",
    "النوع",
    "الكمية",
    "سعر الشراء",
    "اسم المخزن",
    "المقاس"
]

# Cache to store products in memory
products_cache = {}
barcodes_cache = {}

def is_na(val) -> bool:
    """Helper to check if a value is missing (None or NaN)."""
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    return False

def clean_code(val) -> str:
    """Helper to convert product code into clean string representation."""
    if is_na(val):
        return ""
    if isinstance(val, float):
        if val.is_integer():
            return str(int(val)).strip()
        return str(val).strip()
    return str(val).strip()

def format_val(val) -> str:
    """Helper to format Excel field values nicely for display."""
    if is_na(val):
        return ""
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val)

def load_products(is_refresh: bool = False) -> None:
    """Loads the Google Sheet, validates structure, and caches products in memory."""
    global products_cache, barcodes_cache
    logger.info("Google Sheet loading...")
    try:
        url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        response.encoding = 'utf-8'
        
        records = list(csv.DictReader(StringIO(response.text)))
        
        # Standardize headers in each record (e.g. rename "الاسم" or "اسم الصنف" to "اسم")
        if records:
            for record in records:
                if "الاسم" in record and "اسم" not in record:
                    record["اسم"] = record.pop("الاسم")
                if "اسم الصنف" in record and "اسم" not in record:
                    record["اسم"] = record.pop("اسم الصنف")
            headers = list(records[0].keys())
        else:
            headers = []
            
        # Validate required columns
        missing_cols = [col for col in REQUIRED_COLUMNS if col not in headers]
        if missing_cols:
            raise ValueError(f"Missing required columns: {missing_cols}")
            
        # Cache all products in memory
        new_cache = {}
        new_barcode_cache = {}
        for row in records:
            raw_code = row.get("الكود")
            code = clean_code(raw_code)
            if not code:
                continue
                
            product_info = {}
            for col in REQUIRED_COLUMNS:
                product_info[col] = format_val(row.get(col))
                
            if "السعر الجديد" in row:
                product_info["السعر الجديد"] = format_val(row["السعر الجديد"])
            else:
                product_info["السعر الجديد"] = product_info.get("سعر البيع", "")
                
            new_cache[code] = product_info
            
            raw_barcode = row.get("الباركود")
            barcode = clean_code(raw_barcode)
            if barcode:
                new_barcode_cache[barcode] = product_info
            
        products_cache = new_cache
        barcodes_cache = new_barcode_cache
        logger.info("Google Sheet loaded successfully.")
        if is_refresh:
            logger.info("Cache refreshed.")
    except Exception as e:
        if is_refresh:
            logger.error(f"Error during cache refresh: {e}")
            logger.info("Refresh failed.")
        else:
            raise e

async def search_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Telegram message handler that searches the product by code."""
    try:
        if not update.message or not update.message.text:
            return
            
        user_code = update.message.text.strip()
        logger.info(f"Searching product for code: {user_code}")
        
        if user_code.lower() in ("/start", "start"):
            welcome_text = (
                "مرحباً بك في بوت البحث عن المنتجات! 👋\n\n"
                "يرجى إرسال كود المنتج أو الباركود للاستعلام عن تفاصيله وسعره الجديد."
            )
            await update.message.reply_text(welcome_text)
            return
            
        if not user_code.isdigit():
            await update.message.reply_text("⚠️ يرجى إدخال كود أو باركود صحيح (أرقام فقط).")
            return
            
        logger.info("Searching by code...")
        if user_code in products_cache:
            logger.info("Product found by code.")
            p = products_cache[user_code]
            reply_text = (
                f"📦 اسم الصنف: {p['اسم']}\n\n"
                f"🏭 الشركة: {p['الشركة']}\n\n"
                f"💰 السعر: {p['السعر الجديد']}"
            )
            await update.message.reply_text(reply_text)
            logger.info(f"Product found: {user_code}")
        else:
            logger.info("Searching by barcode...")
            if user_code in barcodes_cache:
                logger.info("Product found by barcode.")
                p = barcodes_cache[user_code]
                reply_text = (
                    f"📦 اسم الصنف: {p['اسم']}\n\n"
                    f"🏭 الشركة: {p['الشركة']}\n\n"
                    f"💰 السعر: {p['السعر الجديد']}"
                )
                await update.message.reply_text(reply_text)
                logger.info(f"Product found: {user_code}")
            else:
                logger.info("Product not found.")
                await update.message.reply_text("❌ الكود غير موجود")
                logger.info(f"Product not found: {user_code}")
            
    except Exception as e:
        logger.error(f"Error in search_product message handler: {e}", exc_info=True)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error occurring inside python-telegram-bot."""
    logger.error(f"Telegram runtime error: Exception while handling an update: {context.error}", exc_info=context.error)

def refresh_cache_loop():
    while True:
        time.sleep(REFRESH_INTERVAL_MINUTES * 60)
        load_products(is_refresh=True)

def validate_environment() -> None:
    logger.info("Starting application...")
    if not TOKEN:
        logger.error("Environment validation failed: TOKEN environment variable is missing.")
        raise ValueError("TOKEN environment variable is missing.")
    if not SHEET_ID:
        logger.error("Environment validation failed: SHEET_ID environment variable is missing.")
        raise ValueError("SHEET_ID environment variable is missing.")
    logger.info("Environment loaded...")

def main():
    try:
        validate_environment()
    except ValueError:
        return

    try:
        load_products()
        logger.info("Google Sheet connected...")
    except Exception as e:
        logger.error(f"Startup check failed during Google Sheet loading/validation: {e}")
        return

    # Start background cache refresh thread
    refresh_thread = threading.Thread(target=refresh_cache_loop, daemon=True)
    refresh_thread.start()

    logger.info("Bot started successfully...")
    try:
        app = ApplicationBuilder().token(TOKEN).build()
        
        # Message handler for products search
        app.add_handler(MessageHandler(filters.TEXT, search_product))
        
        # Error handler
        app.add_error_handler(error_handler)
        
        app.run_polling()
    except Exception as e:
        logger.error(f"Telegram runtime error during application startup/execution: {e}", exc_info=True)

if __name__ == "__main__":
    main()
