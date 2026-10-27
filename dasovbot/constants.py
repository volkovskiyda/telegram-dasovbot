# Conversation states
SUBSCRIBE_URL, SUBSCRIBE_PLAYLIST, SUBSCRIBE_SHOW = range(3)
UNSUBSCRIBE_PLAYLIST, = range(1)
MULTIPLE_SUBSCRIBE_URLS, = range(1)
DAS_URL, = range(1)

# Error messages
VIDEO_ERROR_MESSAGES = [
    'This video has been removed for violating',
    'Sign in to confirm your age',
    'Private video',
    'Video unavailable',
    'members-only content',
    "members on level",
]

# yt-dlp failures that clear up on their own: YouTube's bot check (it lifts
# after a while or with a different media URL), an expired or throttled
# download URL (403), rate limiting, server errors. Matched case-insensitively
# against the DownloadError text after VIDEO_ERROR_MESSAGES
TRANSIENT_ERROR_MESSAGES = [
    'Please sign in',
    'sign in to confirm you',  # "you're not a bot", with either apostrophe
    'HTTP Error 403',
    'HTTP Error 429',
    'HTTP Error 50',  # 500, 502, 503, 504
]

# Intervals
INTERVAL_SEC = 60 * 60  # an hour
TIMEOUT_SEC = 60 * 10  # 10 minutes
CONVERSATION_TIMEOUT_SEC = 60 * 10  # end abandoned conversation flows so their state is dropped
RESTART_DELAY_SEC = 60  # delay before restarting a crashed worker loop
PROCESS_INTERVAL_SEC = 1  # breather between consecutive downloads

# Backup monitoring
BACKUP_CHECK_INTERVAL_SEC = 60 * 60  # check backup freshness hourly
BACKUP_STALE_SEC = 26 * 60 * 60  # alert if newest backup is older than this (2x the 12h default cron)

# Disk space: the media, export and data volumes are checked this often and
# raise a health alert (plus one developer message per episode) when free
# space drops under DISK_FREE_MIN_GB (Config.disk_free_min_gb)
DISK_CHECK_INTERVAL_SEC = 60 * 60

# Attempts before a failing intent is dropped
MAX_INTENT_RETRIES = 3

# A transient failure (TRANSIENT_ERROR_MESSAGES) backs the intent off for
# this long and does not count against MAX_INTENT_RETRIES, until the same
# intent has failed that way MAX_TRANSIENT_RETRIES times in a row; from then
# on it is an ordinary failure. 8 x 15 min = about two hours of patience
INTENT_TRANSIENT_BACKOFF_SEC = 15 * 60
MAX_TRANSIENT_RETRIES = 8

# Minimum delay before a failed intent becomes eligible again (it is also
# demoted to priority 0 so it cannot head-of-line-block fresh requests).
# A timed-out download backs off for a full TIMEOUT_SEC instead: it is only
# cancelled at yt-dlp's next callback and may briefly still write its output.
INTENT_RETRY_BACKOFF_SEC = 60

# Media folder sweep: files older than this are leftovers (every download is
# sent within minutes or cleaned up), so they are removed hourly
MEDIA_SWEEP_INTERVAL_SEC = 60 * 60
MEDIA_MAX_AGE_SEC = 6 * 60 * 60

# Retries when Telegram rate-limits a delivery (RetryAfter)
MAX_SEND_RETRIES = 2

# Updates PTB may process at once. Handlers block on yt-dlp metadata
# extraction, so a serial queue let one slow URL stall every other user
MAX_CONCURRENT_UPDATES = 16

# HA / sync (timers live in Config: HEARTBEAT_INTERVAL_SEC, LEASE_TTL_SEC,
# FAILBACK_STABLE_SEC are env-tunable per node)
SYNC_PAGE_SIZE = 500  # rows per /sync/changes page
SNAPSHOT_INTERVAL_SEC = 60 * 60  # passive node pulls a full snapshot hourly (self-healing floor)
SYNC_ERROR_NOTIFY_INTERVAL_SEC = 10 * 60  # developer hears about sync errors at most this often

# VPN status poller against the gluetun control server (services/vpn.py)
VPN_POLL_INTERVAL_SEC = 60
VPN_POLL_TIMEOUT_SEC = 10
READINESS_LEASE_FACTOR = 3  # ready if the last sync is within this many lease TTLs of the last heartbeat
# After a step-down (the peer turned out to be alive) a node refuses the next
# automatic "lease lost" takeover for STEP_DOWN_HOLD_FACTOR lease TTLs, doubling
# per consecutive step-down up to STEP_DOWN_HOLD_MAX_SEC; a flapping link then
# costs one split-brain window per hold-down instead of one per TTL
STEP_DOWN_HOLD_FACTOR = 2
STEP_DOWN_HOLD_MAX_SEC = 15 * 60
TRANSITION_COLLAPSE_SEC = 10 * 60  # an identical transition message within this window is counted, not re-sent
HA_ROLE_ACTIVE, HA_ROLE_PASSIVE, HA_ROLE_DRAINING = 'active', 'passive', 'draining'

# A banned user's request shows the loading animation, then fails after a
# random delay in this range so it looks like an ordinary dead video
BANNED_FAILURE_DELAY_SEC = (10, 60)

# Upload size (MB) above which a failed send falls back to 360p
LARGE_FILE_MB = 2000

# Sources
SOURCE_SUBSCRIPTION = 'subscription'
SOURCE_DOWNLOAD = 'download'
SOURCE_INLINE = 'inline'
SOURCE_API = 'api'

# Format strings
DATETIME_FORMAT = '%Y%m%d_%H%M%S'
DATE_FORMAT = '%Y%m%d'
VIDEO_FORMAT = 'bv*[ext=mp4][filesize_approx<=?2G]'

# Target resolution, applied through format_sort's `res` field instead of a
# height filter. `res` is min(height, width), so it caps portrait and
# landscape alike; a height<=720 filter rejected 720x1280 outright and
# silently pushed every vertical video to the bottom of the ladder.
VIDEO_RES = 720
# Resolution retried when a send exceeds LARGE_FILE_MB
FALLBACK_VIDEO_RES = 360
