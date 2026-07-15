"""Start the dashboard with mock data for local testing."""

import asyncio
import os
import tempfile

from aiohttp import web

from dasovbot.config import Config
from dasovbot.models import Intent, Subscription, TemporaryInlineQuery, VideoInfo
from dasovbot.state import BotState
from dasovbot.dashboard.server import create_app


def mock_ignored(state: BotState):
    """Populate 20 ignored videos with varying title lengths."""
    ignored_intents = [
        ("https://youtube.com/watch?v=abc1", "Lo-fi", "subscription"),
        ("https://youtube.com/watch?v=abc2", "Cat", "download"),
        ("https://youtube.com/watch?v=abc3", "This Is A Moderately Long Video Title That Should Wrap Nicely In The Table", "subscription"),
        ("https://youtube.com/watch?v=abc4", "OK", "download"),
        ("https://youtube.com/watch?v=abc5", "Short clip", "subscription"),
        ("https://youtube.com/watch?v=abc6", "An Extremely Long Title For a YouTube Video That Keeps Going And Going To Test How The Dashboard Handles Truncation And Button Positioning", "download"),
        ("https://youtube.com/watch?v=abc7", "x", "subscription"),
        ("https://youtube.com/watch?v=abc8", "Weekend Vibes Compilation 2024 - Best Moments", "subscription"),
        ("https://youtube.com/watch?v=abc9", "Hi", "download"),
        ("https://youtube.com/watch?v=abc10", "Medium length title here", "subscription"),
        ("https://youtube.com/watch?v=abc11", "A", "download"),
        ("https://youtube.com/watch?v=abc12", "Tutorial: How To Build A Full Stack App With React, Node.js, PostgreSQL, Docker, and Kubernetes From Scratch", "subscription"),
        ("https://youtube.com/watch?v=abc13", "Nope", "download"),
        ("https://youtube.com/watch?v=abc14", "Daily Dose Of Internet #847 - You Won't Believe What This Dog Did Next", "subscription"),
        ("https://youtube.com/watch?v=abc15", "!!", "download"),
    ]
    for url, title, source in ignored_intents:
        state.intents[url] = Intent(
            chat_ids=["111"],
            ignored=True,
            source=source,
            title=title,
        )

    ignored_inline = [
        "funny cat compilation",
        "a]",
        "how to mass produce mass produced mass production lines for mass producing things that produce other things in mass quantities",
        "lofi",
        "??",
    ]
    for query in ignored_inline:
        state.temporary_inline_queries[query] = TemporaryInlineQuery(
            timestamp="2026-04-19T12:00:00",
            ignored=True,
        )


def mock_videos(state: BotState):
    """Populate 80+ videos with diverse data for testing search and pagination."""
    sources = ["subscription", "download", "inline"]
    uploaders = [
        ("LofiGirl", "https://youtube.com/@LofiGirl"),
        ("TechWithTim", "https://youtube.com/@TechWithTim"),
        ("MemeChannel", "https://youtube.com/@MemeChannel"),
        ("Radiohead", "https://youtube.com/@Radiohead"),
        ("ArjanCodes", "https://youtube.com/@ArjanCodes"),
        ("GordonRamsay", "https://youtube.com/@GordonRamsay"),
        ("SpaceX", "https://youtube.com/@SpaceX"),
        ("Fireship", "https://youtube.com/@Fireship"),
        ("3Blue1Brown", "https://youtube.com/@3Blue1Brown"),
        ("Veritasium", "https://youtube.com/@Veritasium"),
    ]
    hand_crafted = [
        ("https://youtube.com/watch?v=dQw4w9WgXcQ", VideoInfo(
            title="Rick Astley - Never Gonna Give You Up",
            description="The official video for Rick Astley's Never Gonna Give You Up",
            file_id="file_001",
            webpage_url="https://youtube.com/watch?v=dQw4w9WgXcQ",
            upload_date="20091025",
            uploader_url="https://youtube.com/@RickAstley",
            duration=213,
            caption="[20091025] Rick Astley - Never Gonna Give You Up\nhttps://youtube.com/watch?v=dQw4w9WgXcQ",
            source="download",
            processed_at="20260418_140000",
        )),
        ("https://youtube.com/watch?v=lofi001", VideoInfo(
            title="lofi hip hop radio - beats to relax/study to",
            description="Lofi Girl live stream with chill beats for studying and relaxing",
            file_id="file_002",
            webpage_url="https://youtube.com/watch?v=lofi001",
            upload_date="20260101",
            uploader_url="https://youtube.com/@LofiGirl",
            duration=3600,
            caption="[20260101] lofi hip hop radio\nhttps://youtube.com/watch?v=lofi001",
            source="subscription",
            processed_at="20260415_083000",
        )),
        ("https://youtube.com/watch?v=tech001", VideoInfo(
            title="Building a Kubernetes Cluster from Scratch",
            description="Step by step tutorial on setting up k8s with kubeadm on bare metal servers",
            file_id="file_003",
            webpage_url="https://youtube.com/watch?v=tech001",
            upload_date="20260310",
            uploader_url="https://youtube.com/@TechWithTim",
            duration=1845,
            caption="[20260310] Building a Kubernetes Cluster from Scratch\nhttps://youtube.com/watch?v=tech001",
            source="subscription",
            processed_at="20260410_120000",
        )),
        ("https://youtube.com/watch?v=cat001", VideoInfo(
            title="Funny Cats Compilation 2026",
            description="The funniest cat videos of 2026 so far",
            file_id="file_004",
            webpage_url="https://youtube.com/watch?v=cat001",
            upload_date="20260201",
            uploader_url="https://youtube.com/@MemeChannel",
            duration=612,
            caption="[20260201] Funny Cats Compilation 2026\nhttps://youtube.com/watch?v=cat001",
            source="download",
            processed_at="20260405_190000",
        )),
        ("https://youtube.com/watch?v=music001", VideoInfo(
            title="Radiohead - Creep (Live at Glastonbury)",
            description="Radiohead performing Creep live at Glastonbury Festival 2003",
            file_id="file_005",
            webpage_url="https://youtube.com/watch?v=music001",
            upload_date="20120815",
            uploader_url="https://youtube.com/@Radiohead",
            duration=285,
            caption="[20120815] Radiohead - Creep (Live at Glastonbury)\nhttps://youtube.com/watch?v=music001",
            source="download",
            processed_at="20260401_220000",
        )),
        ("https://vimeo.com/123456", VideoInfo(
            title="Short Film: The Last Pixel",
            description="An award-winning animated short about the last pixel on a dying screen",
            file_id="file_006",
            webpage_url="https://vimeo.com/123456",
            upload_date="20251220",
            uploader_url="https://vimeo.com/pixelstudio",
            duration=480,
            caption="[20251220] Short Film: The Last Pixel\nhttps://vimeo.com/123456",
            source="inline",
            processed_at="20260319_100000",
        )),
        ("https://youtube.com/watch?v=py001", VideoInfo(
            title="Python AsyncIO Deep Dive",
            description="Advanced asyncio patterns: tasks, gather, semaphores, and event loops explained",
            file_id="file_007",
            webpage_url="https://youtube.com/watch?v=py001",
            upload_date="20260405",
            uploader_url="https://youtube.com/@ArjanCodes",
            duration=1520,
            caption="[20260405] Python AsyncIO Deep Dive\nhttps://youtube.com/watch?v=py001",
            source="subscription",
            processed_at="20260412_093000",
        )),
        ("https://youtube.com/watch?v=cook001", VideoInfo(
            title="Gordon Ramsay's Perfect Scrambled Eggs",
            description="Master Chef Gordon Ramsay shows how to make the perfect scrambled eggs",
            file_id="file_008",
            webpage_url="https://youtube.com/watch?v=cook001",
            upload_date="20180314",
            uploader_url="https://youtube.com/@GordonRamsay",
            duration=245,
            caption="[20180314] Gordon Ramsay's Perfect Scrambled Eggs\nhttps://youtube.com/watch?v=cook001",
            source="download",
            processed_at="20260318_160000",
        )),
        ("https://youtube.com/watch?v=game001", VideoInfo(
            title="Elden Ring DLC - All Boss Fights (No Damage)",
            description="Shadow of the Erdtree complete boss rush without taking damage",
            file_id="file_009",
            webpage_url="https://youtube.com/watch?v=game001",
            upload_date="20260115",
            uploader_url="https://youtube.com/@LetsPlayGamer",
            duration=7200,
            caption="[20260115] Elden Ring DLC - All Boss Fights\nhttps://youtube.com/watch?v=game001",
            source="inline",
            processed_at="20260315_200000",
        )),
        ("https://youtube.com/watch?v=space001", VideoInfo(
            title="SpaceX Starship Launch - Full Replay",
            description="Complete replay of the SpaceX Starship orbital test flight",
            file_id="file_010",
            webpage_url="https://youtube.com/watch?v=space001",
            upload_date="20260401",
            uploader_url="https://youtube.com/@SpaceX",
            duration=5400,
            caption="[20260401] SpaceX Starship Launch\nhttps://youtube.com/watch?v=space001",
            source="subscription",
            processed_at="20260419_070000",
        )),
        # Video without file_id — should NOT appear in the dashboard
        ("https://youtube.com/watch?v=pending001", VideoInfo(
            title="This Video Is Still Processing",
            description="Should be invisible on the videos page",
            duration=100,
            source="download",
        )),
    ]
    for url, info in hand_crafted:
        state.videos[url] = info

    # Generate 70 more videos to exceed default limit of 50
    generated_titles = [
        "React 19 Server Components Explained",
        "Why SQL is Still King in 2026",
        "Rust vs Go: Which Should You Learn?",
        "Making Sourdough Bread at Home",
        "10 Git Commands Every Dev Should Know",
        "The Math Behind Neural Networks",
        "Street Food Tour in Bangkok",
        "How Boeing 747 Engines Work",
        "Fixing My Vintage Synthesizer",
        "Learn Docker in 12 Minutes",
        "Why Gravity is NOT a Force",
        "Making a Game in 48 Hours",
        "The History of the Internet",
        "Piano Tutorial: Clair de Lune",
        "Assembly Language in 100 Seconds",
        "How I Built a Smart Home for $200",
        "Vue.js 4 First Look",
        "Extreme Mountain Biking POV",
        "Every Design Pattern Explained",
        "How CRISPR Actually Works",
        "Making Ramen from Scratch",
        "Linux Kernel Internals Deep Dive",
        "World's Largest Telescope Tour",
        "Mechanical Keyboard Build Log",
        "The Science of Black Holes",
        "Full Stack App in 30 Minutes",
        "Best Jazz Albums of All Time",
        "How CPUs Actually Work",
        "Urban Sketching Tutorial",
        "What is Quantum Computing Really",
        "Baking French Macarons at Home",
        "Neovim Config from Scratch",
        "The Physics of Music",
        "Solo Camping in the Mountains",
        "PostgreSQL Performance Tuning",
        "Oil Painting for Beginners",
        "How DNS Works Step by Step",
        "Restoring a 1960s Motorcycle",
        "Category Theory for Programmers",
        "World's Best Coffee Brewing Methods",
        "Building a Ray Tracer in C",
        "Northern Lights Time Lapse 4K",
        "Type Systems Explained Simply",
        "How to Train for a Marathon",
        "Redis Crash Course 2026",
        "The Fermi Paradox Explained",
        "DIY Standing Desk Build",
        "GraphQL vs REST in Practice",
        "Underwater Volcano Documentary",
        "Writing a Compiler from Scratch",
        "Japanese Woodworking Techniques",
        "How WiFi Actually Works",
        "Street Photography Tips",
        "WebAssembly: The Future of Web",
        "Hiking the Pacific Crest Trail",
        "How Transformers Changed AI",
        "Building a Drone from Parts",
        "The Riemann Hypothesis Explained",
        "Cooking Perfect Steak Every Time",
        "System Design Interview Prep",
        "How Vinyl Records Are Made",
        "Functional Programming in Python",
        "Freediving World Record Attempt",
        "How Git Works Under the Hood",
        "Origami Dragon Tutorial",
        "Microservices Anti-Patterns",
        "The Science of Sleep",
        "Building an 8-bit Computer",
        "Fermentation Science Explained",
        "Kubernetes Security Best Practices",
    ]
    for i, title in enumerate(generated_titles):
        n = i + 11  # start after hand-crafted file_010
        uploader_name, uploader_url = uploaders[i % len(uploaders)]
        source = sources[i % len(sources)]
        # Spread upload dates across 2025-2026
        month = (i % 12) + 1
        day = (i % 28) + 1
        year = 2025 if i < 35 else 2026
        upload_date = f"{year}{month:02d}{day:02d}"
        # Spread processed_at across Jan-Apr 2026
        p_month = (i % 4) + 1
        p_day = (i % 28) + 1
        p_hour = (i * 3) % 24
        processed_at = f"2026{p_month:02d}{p_day:02d}_{p_hour:02d}0000"
        duration = 60 + (i * 47) % 3600

        url = f"https://youtube.com/watch?v=gen{n:03d}"
        state.videos[url] = VideoInfo(
            title=title,
            description=f"Description for: {title}",
            file_id=f"file_{n:03d}",
            webpage_url=url,
            upload_date=upload_date,
            uploader_url=uploader_url,
            duration=duration,
            caption=f"[{upload_date}] {title}\n{url}",
            source=source,
            processed_at=processed_at,
        )


async def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(f"{tmpdir}/data", exist_ok=True)

        config = Config(
            bot_token="fake",
            base_url="http://localhost",
            developer_chat_id="111",
            developer_id="111",
            config_folder=tmpdir,
        )
        state = await BotState.create(config)

        # Mock users
        state.users = {
            "111": {"first_name": "Dmitrii", "last_name": "Volkovskiy", "username": "volkovskiyda"},
            "222": {"first_name": "Alice", "username": "alice_wonder"},
            "333": {"first_name": "Bob", "last_name": "Smith"},
            "444": {},  # user with no name — should fallback to chat_id
        }

        # Mock subscriptions
        state.subscriptions = {
            "https://youtube.com/playlist?list=PL_music": Subscription(
                chat_ids=["111", "222", "333"],
                title="Chill Beats",
                uploader="LofiGirl",
            ),
            "https://youtube.com/playlist?list=PL_tech": Subscription(
                chat_ids=["111", "444"],
                title="Tech Talks",
                uploader="Google",
            ),
            "https://youtube.com/playlist?list=PL_funny": Subscription(
                chat_ids=["222", "333", "444"],
                title="Funny Compilations",
                uploader="MemeChannel",
            ),
            "https://youtube.com/playlist?list=PL_solo": Subscription(
                chat_ids=["111"],
                title="Solo Playlist",
                uploader="SoloCreator",
            ),
        }

        mock_ignored(state)
        mock_videos(state)

        os.environ.setdefault("DASHBOARD_PASSWORD", "test")
        app = create_app(state)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 8080)
        await site.start()
        print("Dashboard running at http://localhost:8080  (password: test)")
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
