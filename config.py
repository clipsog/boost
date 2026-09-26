import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent

VIEWS_SERVICE_ID = int(os.getenv("ZEFAME_VIEWS_SERVICE", "953"))
VIEWS_QUANTITY = int(os.getenv("ZEFAME_VIEWS_QUANTITY", "542"))
LOW_VIEWS_QUANTITY = int(os.getenv("ZEFAME_LOW_VIEWS_QUANTITY", "300"))
# Likes: auto picks panel using Zefame site maintenance list + API catalogs. See LIKES_PANEL.
BOOSTERO_LIKES_SERVICE_ID = int(os.getenv("BOOSTERO_LIKES_SERVICE", "4802"))
ZEFAME_LIKES_SERVICE_ID = int(os.getenv("ZEFAME_LIKES_SERVICE", "988"))
LIKES_PANEL = os.getenv("LIKES_PANEL", "auto").strip().lower()
LIKES_SERVICE_ID = ZEFAME_LIKES_SERVICE_ID
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
QUEUE_MAX_FETCH = int(os.getenv("ZEFAME_QUEUE_MAX_FETCH", "120"))
# Posts at least this old are treated as already boosted in queue/history (no new full pack).
ASSUMED_BOOST_HOURS = float(os.getenv("ZEFAME_ASSUMED_BOOST_HOURS", "8"))
# Same Eastern calendar day: posts before this hour (24h) count as already boosted.
_assumed_et_hour = os.getenv("ZEFAME_ASSUMED_BOOST_ET_CUTOFF_HOUR", "17").strip()
ASSUMED_BOOST_ET_CUTOFF_HOUR: int | None = (
    int(_assumed_et_hour) if _assumed_et_hour else None
)
# After cutoff, only same-day posts before this ET hour are auto-marked (morning backlog).
_morning_et_hour = os.getenv("ZEFAME_ASSUMED_BOOST_MORNING_ET_HOUR", "12").strip()
ASSUMED_BOOST_MORNING_ET_HOUR: int | None = (
    int(_morning_et_hour) if _morning_et_hour else None
)
# If a post is old enough and public stats look like a full pack landed, mark it boosted.
INFER_BOOST_MIN_AGE_HOURS = float(os.getenv("ZEFAME_INFER_BOOST_MIN_AGE_HOURS", "2"))
INFER_BOOST_VIEWS_MIN = int(os.getenv("ZEFAME_INFER_BOOST_VIEWS_MIN", "500"))
INFER_BOOST_LIKES_MIN = int(os.getenv("ZEFAME_INFER_BOOST_LIKES_MIN", "18"))

_boost_history_raw = os.getenv("BOOST_HISTORY_PATH", "").strip()
BOOST_HISTORY_PATH = (
    Path(_boost_history_raw).expanduser()
    if _boost_history_raw
    else _PROJECT_ROOT / "boosted_videos.json"
)
_seed_raw = os.getenv("BOOST_HISTORY_SEED_PATH", "").strip()
BOOST_HISTORY_SEED_PATH = (
    Path(_seed_raw).expanduser()
    if _seed_raw
    else _PROJECT_ROOT / "data" / "boost_history_seed.json"
)
BOOST_ADMIN_SECRET = os.getenv("BOOST_ADMIN_SECRET", "").strip()
