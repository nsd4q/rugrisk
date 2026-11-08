import os
import sys

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SECRET_KEY", "sb_secret_test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
