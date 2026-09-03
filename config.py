import os

VIEWS_SERVICE_ID = int(os.getenv("ZEFAME_VIEWS_SERVICE", "953"))
VIEWS_QUANTITY = int(os.getenv("ZEFAME_VIEWS_QUANTITY", "542"))
LOW_VIEWS_QUANTITY = int(os.getenv("ZEFAME_LOW_VIEWS_QUANTITY", "300"))
LIKES_SERVICE_ID = int(os.getenv("ZEFAME_LIKES_SERVICE", "1089"))
LIKES_QUANTITY = int(os.getenv("ZEFAME_LIKES_QUANTITY", "10"))
FULL_LIKES_MIN = int(os.getenv("ZEFAME_FULL_LIKES_MIN", "10"))
FULL_LIKES_MAX = int(os.getenv("ZEFAME_FULL_LIKES_MAX", "14"))
FULL_VIEWS_LOW = int(os.getenv("ZEFAME_FULL_VIEWS_LOW", "480"))  # video has 0 likes
FULL_VIEWS_MID = int(os.getenv("ZEFAME_FULL_VIEWS_MID", "541"))  # video has 1–7 likes
FULL_VIEWS_HIGH = int(os.getenv("ZEFAME_FULL_VIEWS_HIGH", "650"))  # video has 8–19 likes
FULL_VIEWS_ULTRA = int(os.getenv("ZEFAME_FULL_VIEWS_ULTRA", "750"))  # video has ≥20 likes
VIEWS_ONLY_QUANTITY = int(os.getenv("ZEFAME_VIEWS_ONLY_QUANTITY", "100"))
VIEWS_MIN = int(os.getenv("ZEFAME_VIEWS_MIN", "100"))
LIKES_ONLY_QUANTITY = int(os.getenv("ZEFAME_LIKES_ONLY_QUANTITY", "12"))
LIKES_MIN = int(os.getenv("ZEFAME_LIKES_MIN", "10"))
QUEUE_PROFILE = os.getenv("ZEFAME_QUEUE_PROFILE", "the.clips.og").strip().lstrip("@")
QUEUE_LOOKBACK_HOURS = int(os.getenv("ZEFAME_QUEUE_LOOKBACK_HOURS", "24"))
QUEUE_MIN_AGE_HOURS = int(os.getenv("ZEFAME_QUEUE_MIN_AGE_HOURS", "1"))
