# Conversation states
SUBSCRIBE_URL, SUBSCRIBE_PLAYLIST, SUBSCRIBE_SHOW = range(3)
UNSUBSCRIBE_PLAYLIST, = range(1)
MULTIPLE_SUBSCRIBE_URLS = range(1)
DAS_URL, = range(1)

# Error messages
VIDEO_ERROR_MESSAGES = [
    'This video has been removed for violating',
    'Sign in to confirm your age',
    'Private video',
    'Video unavailable',
]

# Intervals
INTERVAL_SEC = 60 * 60  # an hour
TIMEOUT_SEC = 60 * 10  # 10 minutes

# Sources
SOURCE_SUBSCRIPTION = 'subscription'
SOURCE_DOWNLOAD = 'download'
SOURCE_INLINE = 'inline'

# Format strings
DATETIME_FORMAT = '%Y%m%d_%H%M%S'
DATE_FORMAT = '%Y%m%d'
VIDEO_FORMAT = 'bv*[ext=mp4][height<=?720][filesize_approx<=?2G]'
