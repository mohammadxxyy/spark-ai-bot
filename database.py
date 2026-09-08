import sqlite3
import random
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spark_marketing.db")


def get_connection() -> sqlite3.Connection:
    """إنشاء اتصال بقاعدة البيانات مع تفعيل الصفوف كقواميس"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """تهيئة جداول قاعدة البيانات الخاصة بطلبات التسويق والحملات"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_code TEXT UNIQUE NOT NULL,
            user_id INTEGER,
            user_name TEXT,
            client_name TEXT,
            business_name TEXT,
            service_type TEXT,
            platforms TEXT,
            budget_goal TEXT,
            contact_phone TEXT,
            status TEXT DEFAULT 'جديد - قيد المراجعة والتحليل',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def generate_order_code() -> str:
    """توليد كود مميز للطلب بصيغة SPARK-MKT-XXXX"""
    random_num = random.randint(1000, 9999)
    return f"SPARK-MKT-{random_num}"


def save_order(
    user_id: int,
    user_name: str,
    client_name: str,
    business_name: str,
    service_type: str,
    platforms: str,
    budget_goal: str,
    contact_phone: str,
) -> str:
    """حفظ طلب حملة إعلانية جديد في قاعدة البيانات وإرجاع كود الطلب"""
    conn = get_connection()
    cursor = conn.cursor()

    # محاولة إنشاء كود فريد غير مكرر
    for _ in range(10):
        order_code = generate_order_code()
        cursor.execute("SELECT id FROM campaign_orders WHERE order_code = ?", (order_code,))
        if not cursor.fetchone():
            break

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        """
        INSERT INTO campaign_orders (
            order_code, user_id, user_name, client_name, business_name,
            service_type, platforms, budget_goal, contact_phone, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_code,
            user_id,
            user_name,
            client_name,
            business_name,
            service_type,
            platforms,
            budget_goal,
            contact_phone,
            created_at,
        ),
    )
    conn.commit()
    conn.close()
    return order_code


def get_order_by_code(order_code: str) -> Optional[Dict[str, Any]]:
    """البحث عن تفاصيل طلب معين بواسطة كود الطلب"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM campaign_orders WHERE UPPER(order_code) = UPPER(?)", (order_code.strip(),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_orders_by_user(user_id: int) -> List[Dict[str, Any]]:
    """جلب جميع طلبات مستخدم معين بواسطة User ID الخاص به"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM campaign_orders WHERE user_id = ? ORDER BY id DESC LIMIT 5",
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_recent_orders(limit: int = 10) -> List[Dict[str, Any]]:
    """جلب آخر الطلبات المسجلة في النظام للوحة تحكم الإدارة"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM campaign_orders ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_order_status(order_code: str, new_status: str) -> bool:
    """تحديث حالة طلب معين"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE campaign_orders SET status = ? WHERE UPPER(order_code) = UPPER(?)",
        (new_status, order_code.strip()),
    )
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_orders_stats() -> Dict[str, Any]:
    """جلب إحصائيات متقدمة للطلبات والحملات المسجلة"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) as total FROM campaign_orders")
    total_orders = cursor.fetchone()["total"]
    
    cursor.execute("SELECT COUNT(DISTINCT user_id) as users FROM campaign_orders")
    total_clients = cursor.fetchone()["users"]
    
    cursor.execute("SELECT COUNT(*) as active FROM campaign_orders WHERE status LIKE '%نشطة%' OR status LIKE '%إطلاق%'")
    active_campaigns = cursor.fetchone()["active"]

    cursor.execute("SELECT COUNT(*) as contacted FROM campaign_orders WHERE status LIKE '%تواصل%' OR status LIKE '%استلام%'")
    contacted_orders = cursor.fetchone()["contacted"]
    
    cursor.execute("SELECT service_type, COUNT(*) as cnt FROM campaign_orders GROUP BY service_type ORDER BY cnt DESC LIMIT 1")
    top_service_row = cursor.fetchone()
    top_service = top_service_row["service_type"] if top_service_row else "إعلانات ممولة"
    
    conn.close()
    return {
        "total_orders": total_orders,
        "total_clients": total_clients,
        "active_campaigns": active_campaigns,
        "contacted_orders": contacted_orders,
        "top_service": top_service,
    }


# تهيئة قاعدة البيانات فور استيراد الملف
init_db()
