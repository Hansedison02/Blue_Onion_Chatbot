import tkinter as tk
from tkinter import Entry, Button, Frame, Label, Canvas, Scrollbar, filedialog
from PIL import Image, ImageTk, ImageDraw, ImageFont
import ollama
import requests
from bs4 import BeautifulSoup
import urllib.parse
import time
import os
import csv
import threading
import queue
import json
from datetime import datetime
from urllib.parse import urlparse, parse_qs

# ----------------------- JSON Logging Setup -----------------------
LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "Logs",
    "blue_onion_chat_log.jsonl"
)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def log_to_json(event_type: str, details: dict):
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "event_type": event_type,
        **details
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            json.dump(log_entry, f, ensure_ascii=False)
            f.write("\n")
    except Exception:
        pass

# ----------------------- YouTube Transcript Import -----------------------
try:
    from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
except ImportError:
    log_to_json("startup_warning", {"message": "youtube-transcript-api is not installed."})

# ----------------------- Configuration -----------------------
MODEL = "mistral-nemo:latest"

PROFILE_PATH = r"C:\Users\ThinkPad\Blue-Onion\Pictures\Profile\Blue.png"
SEND_ICON_PATH = r"C:\Users\ThinkPad\Blue-Onion\Pictures\Icons\Send.png"

DEFAULT_CSV_PATH = r"C:\Users\ThinkPad\Blue-Onion\Data_Geopolitics_Blue_Onion.csv"

# Colors
BG_DARK = "#36393f"
HEADER_BG = "#2f3136"
BOT_BUBBLE = "#e0e0e0"
USER_BUBBLE = "#7f8c8d"
TEXT_LIGHT = "white"
TEXT_DARK = "black"

# ----------------------- Tools Definition -----------------------
tools = [
    {
        "type": "function",
        "function": {
            "name": "scrape_webpage",
            "description": "MUST be used when user says 'scrap this', 'scrape this', 'summarize this article', 'read this', or pastes ANY direct URL. This is the highest priority tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The exact full URL provided by the user (e.g. https://www.bbc.com/news/articles/cdxd88r2wjzo)"
                    }
                },
                "required": ["url"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web_news",
            "description": "Search for recent news articles from reliable sources. Use ONLY for general queries, NOT when a direct URL is provided.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query"}},
                "required": ["query"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_youtube_transcript",
            "description": "Fetch the full transcript of a YouTube video.",
            "parameters": {
                "type": "object",
                "properties": {"video_url": {"type": "string"}},
                "required": ["video_url"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_timestamp",
            "description": "Get the current date and time.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }
]

# ----------------------- CSV Loader -----------------------
def load_csv_to_text(csv_path: str) -> str:
    if not os.path.exists(csv_path):
        return f"No CSV data found at: {csv_path}"
    try:
        data_text = "=== GEOPOLITICAL DATA FROM CSV ===\n"
        with open(csv_path, mode='r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for i, row in enumerate(reader):
                if i > 100:
                    data_text += "\n... (more rows truncated for context limits) ...\n"
                    break
                data_text += f"Row {i+1}: {dict(row)}\n"
        return data_text
    except Exception as e:
        return f"Error loading CSV: {str(e)}"

default_data = load_csv_to_text(DEFAULT_CSV_PATH)

# ----------------------- STRONGER SYSTEM PROMPT -----------------------
def build_system_prompt(csv_data: str, is_upload: bool = False) -> str:
    source = "from user upload" if is_upload else "loaded from default file"
    return f"""You are Blue Onion, a highly knowledgeable, neutral, and precise AI specialist in **Geopolitics and International Security**.

**CORE RULES**:
- Stay strictly within geopolitics and international security.
- Be factual, analytical, and balanced. Avoid moralizing or ideological bias.
- Never hallucinate sources, events, or data. If uncertain, say so.
- You must answer only questions directly related to geopolitics and international security.
- Treat any non-geopolitics topic as out of scope, even if it appears in a geopolitical context unless the core question is geopolitical.
- When uncertain, classify as out of scope.
- Never infer that a topic is allowed unless it is explicitly in scope.
- If the prompt is given in different language, analyse as if it's related to **GEOPOLITICS SCOPE** provided below first
- If the user asks about topics clearly outside geopolitics and international security, reply: "I'm sorry, I cannot discuss the information further."
- Make exceptions for Youtube URL, to provide tool call first, then re-analyse it to determine it's geopolitical or not.

**TOOL RULES**:
- Use `scrape_webpage` for any direct URL or when asked to summarize a specific article/page.
- Use `search_web_news` for general news, current events, or broad searches.
- Use `get_youtube_transcript` when the user provides a YouTube URL.
- Always prioritize primary sources and recent information when possible.

**GEOPOLITICS SCOPE** includes:
- International relations, diplomacy, alliances, and great power competition
- Military conflicts, wars, offensives, and strategic analysis (e.g., Russian-Ukrainian war, Middle East conflicts, etc.)
- Intelligence agencies (CIA, FSB, MI6, Mossad, RAW, etc.) and covert operations
- Defense policy, hybrid warfare, sanctions, economic statecraft
- Political geography, energy politics, supply chains, and maritime security

CSV DATA ({source}):
{csv_data}

You are now ready."""

# ----------------------- History -----------------------
history = [
    {
        "role": "system",
        "content": build_system_prompt(default_data, is_upload=False)
    },
    {
        "role": "assistant",
        "content": "Hi! I am Blue Onion, your Assistant for Everyday news regarding Geopolitics. How can I help you today?"
    }
]

response_queue = queue.Queue()

# ----------------------- Tool Functions -----------------------
def get_current_timestamp() -> str:
    now = datetime.now()
    return f"""**Current Timestamp**
- ISO 8601: {now.isoformat()}
- Readable: {now.strftime("%A, %B %d, %Y at %I:%M %p")}"""

def scrape_webpage(url: str) -> str:
    """Fetch and clean any news article."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()

        article = (soup.find("article") or soup.find("main") or 
                   soup.find("div", {"class": lambda x: x and ("article" in str(x).lower() or "story" in str(x).lower())}))

        text = article.get_text(separator="\n", strip=True) if article else soup.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        content = "\n".join(lines[:70])

        title = soup.find("title").get_text(strip=True) if soup.find("title") else "Untitled Article"

        return f"""**✅ Article Scraped Successfully**

**Title:** {title}
**URL:** {url}

**Content:**
{content}

For more updates and analysis on geopolitical events, stay tuned!"""
    except Exception as e:
        log_to_json("scrape_error", {"url": url, "error": str(e)})
        return f"❌ Error scraping webpage: {str(e)}"

def perform_web_search(query: str) -> str:
    """Improved web search using official duckduckgo-search library."""
    from ddgs import DDGS
    import time
    
    search_query = query.strip() or "geopolitics"
    try:
        with DDGS() as ddgs:
            for attempt in range(3):
                try:
                    results = ddgs.text(
                        query=search_query,
                        region="wt-wt",
                        safesearch="off",
                        timelimit=None,
                        max_results=8
                    )
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(0.5)

        if not results:
            return "No results found."

        formatted = [
            f"**{r.get('title', 'No title')}**\n"
            f"{r.get('href', 'No link')}\n"
            f"{r.get('body', 'No snippet')}\n"
            for r in results
        ]
        return "\n\n".join(formatted)
    except Exception as e:
        log_to_json("search_error", {"error": str(e), "query": search_query})
        return f"Search error: {str(e)}"


from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

def get_youtube_transcript(video_url: str) -> str:
    try:
        # 1. Robust Video ID extraction handling standard, short, mobile, and shorts URLs
        video_url = video_url.strip()
        parsed_url = urlparse(video_url)
        
        if parsed_url.hostname in ('youtu.be', 'www.youtu.be'):
            video_id = parsed_url.path[1:]
        elif parsed_url.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
            if parsed_url.path.startswith('/shorts/'):
                video_id = parsed_url.path.split('/')[2]
            else:
                query_params = parse_qs(parsed_url.query)
                video_id = query_params.get('v', [None])[0]
        else:
            # Fallback if raw 11-character video ID is passed directly
            video_id = video_url

        if not video_id:
            return "Error: Valid YouTube Video ID could not be extracted from the URL."

        # 2. Extract transcript using native manual-to-generated fallback mechanism
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        
        # find_transcript automatically prefers manual and falls back to generated
        transcript = transcript_list.find_transcript(['en'])
        transcript_data = transcript.fetch()
        
        # 3. Use the corrected dot notation attribute access
        full_text = " ".join([entry.text for entry in transcript_data])
        
        # 4. Optional: Cleanly truncate on whole words rather than snapping mid-character
        max_chars = 8000
        if len(full_text) > max_chars:
            full_text = full_text[:max_chars].rsplit(' ', 1)[0] + "... [Truncated]"
            
        return f"Transcript for video {video_id}:\n\n{full_text}"
    
    except (TranscriptsDisabled, NoTranscriptFound):
        return "No English transcript available for this video (disabled or not found)."
    except Exception as e:
        return f"Error fetching YouTube transcript: {str(e)}"
# ----------------------- AI Processing Thread -----------------------

def ai_processing_thread(history_for_thread):
    full_response = ""
    try:
        log_to_json("processing_start", {"history_length": len(history_for_thread)})

        while True:
            log_to_json("api_payload", {"model": MODEL, "messages": history_for_thread, "tools": tools})

            res = ollama.chat(model=MODEL, messages=history_for_thread, tools=tools)
            log_to_json("ollama_raw_response", {"full_response": res})

            message = res["message"]

            if message.get("tool_calls"):
                tool_call = message["tool_calls"][0]
                tool_name = tool_call["function"]["name"]
                args = tool_call["function"]["arguments"]

                log_to_json("model_tool_call", {"tool_name": tool_name, "arguments": args, "full_tool_call_object": tool_call})

                if tool_name == "scrape_webpage":
                    url = args.get("url", "")
                    searching_text = f"📄 Scraping article..."
                    tool_result = scrape_webpage(url)
                elif tool_name == "search_web_news":
                    query = args.get("query", "geopolitics")
                    searching_text = f"🔎 Searching news about: {query}..."
                    tool_result = perform_web_search(query)
                elif tool_name == "get_youtube_transcript":
                    video_url = args.get("video_url", "")
                    searching_text = "Fetching YouTube transcript..."
                    tool_result = get_youtube_transcript(video_url)
                elif tool_name == "get_current_timestamp":
                    searching_text = "📅 Retrieving current timestamp..."
                    tool_result = get_current_timestamp()
                else:
                    searching_text = "Processing tool..."
                    tool_result = "Error: Unknown tool."

                log_to_json("tool_result", {"tool_name": tool_name, "result_summary": tool_result[:800] + ("..." if len(tool_result) > 800 else "")})

                response_queue.put(("searching", searching_text))

                history_for_thread.append(message)
                history_for_thread.append({"role": "tool", "content": tool_result, "name": tool_name})
            else:
                full_response = message["content"]
                log_to_json("final_response", {"content": full_response})
                break
    except Exception as e:
        log_to_json("error", {"error_message": str(e)})
        full_response = f"Error: {str(e)}"

    history_for_thread.append({"role": "assistant", "content": full_response})
    response_queue.put(("final", full_response, history_for_thread))

# ----------------------- Main Window -----------------------
root = tk.Tk()
root.title("CHATROOM - Blue Onion Geopolitics")
root.geometry("520x700")
root.configure(bg=BG_DARK)
root.minsize(400, 600)

# Load images
profile_img = Image.open(PROFILE_PATH)
logo_img = profile_img.resize((70, 70))
profile_small = profile_img.resize((45, 45))

logo_photo = ImageTk.PhotoImage(logo_img)
profile_photo = ImageTk.PhotoImage(profile_small)
send_img = Image.open(SEND_ICON_PATH).resize((35, 35))
send_photo = ImageTk.PhotoImage(send_img)

root.logo_photo = logo_photo
root.profile_photo = profile_photo
root.send_photo = send_photo

# ----------------------- Header -----------------------
header = Frame(root, bg=HEADER_BG, height=90)
header.pack(fill="x")
header.pack_propagate(False)

logo_label = Label(header, image=logo_photo, bg=HEADER_BG)
logo_label.pack(side="left", padx=20, pady=10)

title_label = Label(header, text="CHATROOM", font=("GenEi Kiwami Gothic Ultra", 22), fg="white", bg=HEADER_BG)
title_label.pack(side="left", pady=10)

# ----------------------- Chat Container -----------------------
chat_container = Frame(root, bg=BG_DARK)
chat_container.pack(fill="both", expand=True, padx=10, pady=10)

chat_area = Frame(chat_container, bg=BG_DARK)
chat_area.pack(fill="both", expand=True)

canvas = Canvas(chat_area, bg=BG_DARK, highlightthickness=0)
scrollbar = Scrollbar(chat_area, orient="vertical", command=canvas.yview, bg=BG_DARK)
canvas.configure(yscrollcommand=scrollbar.set)

scrollable_frame = Frame(canvas, bg=BG_DARK)
canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
canvas.pack(side="left", fill="both", expand=True)
scrollbar.pack(side="right", fill="y")

scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

# ----------------------- Bubble Image Creator -----------------------
def create_bubble_image(text: str, bg_color: str, fg_color: str, is_user: bool = False):
    if not text.strip():
        text = " "

    padding = 18
    radius = 22
    font_size = 13
    max_text_width = 320

    try:
        font = ImageFont.truetype(r"C:\Users\ThinkPad\Blue-Onion\Fonts\Cascadia_Mono\static\CascadiaMono-Regular.ttf", font_size)
    except IOError:
        try:
            font = ImageFont.truetype("Helvetica", font_size)
        except IOError:
            font = ImageFont.load_default()
            font_size = 18

    ascent, descent = font.getmetrics()
    line_height = ascent + descent
    line_spacing = 6

    lines = []
    words = text.split()
    current_line = []
    current_width = 0
    for word in words:
        word_bbox = font.getbbox(word)
        word_width = word_bbox[2] - word_bbox[0]
        space_width = font.getbbox(" ")[2] if current_line else 0

        if current_width + space_width + word_width <= max_text_width:
            current_line.append(word)
            current_width += space_width + word_width
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
            current_width = word_width
            if word_width > max_text_width:
                lines.append(word)
                current_line = []
                current_width = 0

    if current_line:
        lines.append(" ".join(current_line))

    if not lines:
        lines = [" "]

    line_widths = [font.getbbox(line)[2] - font.getbbox(line)[0] for line in lines]
    text_width = max(line_widths, default=60)
    text_height = line_height * len(lines) + line_spacing * max(0, len(lines) - 1)

    bubble_width = text_width + 2 * padding
    bubble_height = text_height + 2 * padding

    img_width = bubble_width + 2 * radius
    img_height = bubble_height + 2 * radius

    img = Image.new("RGBA", (img_width, img_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, img_width - 1, img_height - 1), radius=radius, fill=bg_color)

    current_y = radius + padding
    for line in lines:
        line_width = font.getbbox(line)[2] - font.getbbox(line)[0]
        x = img_width - radius - padding - line_width if is_user else radius + padding
        draw.text((x, current_y), line, fill=fg_color, font=font)
        current_y += line_height + line_spacing

    return ImageTk.PhotoImage(img)

# ----------------------- Add Message Function -----------------------
def add_message(role: str, content: str):
    msg_frame = Frame(scrollable_frame, bg=BG_DARK)
    
    if role == "assistant":
        msg_frame.pack(anchor="w", fill="x", pady=8, padx=5)
        pic = Label(msg_frame, image=profile_photo, bg=BG_DARK)
        pic.pack(side="left", padx=(0, 10))
        photo = create_bubble_image(content, BOT_BUBBLE, TEXT_DARK, is_user=False)
        bubble = Label(msg_frame, image=photo, bg=BG_DARK)
        bubble.image = photo
        bubble.pack(side="left")
    else:
        msg_frame.pack(anchor="e", fill="x", pady=8, padx=5)
        photo = create_bubble_image(content, USER_BUBBLE, TEXT_LIGHT, is_user=True)
        bubble = Label(msg_frame, image=photo, bg=BG_DARK)
        bubble.image = photo
        bubble.pack(side="right")
    
    canvas.update_idletasks()
    canvas.yview_moveto(1.0)

# Display initial greeting
add_message("assistant", history[-1]["content"])

# ----------------------- Queue Checker (keeps UI responsive) -----------------------
def start_queue_checker(response_label):
    """Polls the queue every 30 ms - keeps UI fully responsive"""
    try:
        while True:
            item = response_queue.get_nowait()
            if item[0] == "searching":
                searching_text = item[1]
                photo = create_bubble_image(searching_text, BOT_BUBBLE, TEXT_DARK, False)
                response_label.config(image=photo)
                response_label.image = photo
                root.update_idletasks()
                canvas.yview_moveto(1.0)
            elif item[0] == "final":
                full_response = item[1]
                updated_history = item[2]
                type_out_response(response_label, full_response, updated_history)
                return
    except queue.Empty:
        pass
    root.after(30, lambda: start_queue_checker(response_label))

def type_out_response(response_label, full_response, updated_history):
    """Typing animation (runs in main thread after AI is done)"""
    current_text = ""
    for char in full_response:
        current_text += char
        photo = create_bubble_image(current_text, BOT_BUBBLE, TEXT_DARK, False)
        response_label.config(image=photo)
        response_label.image = photo
        root.update_idletasks()
        time.sleep(0.005)

    photo = create_bubble_image(full_response, BOT_BUBBLE, TEXT_DARK, False)
    response_label.config(image=photo)
    response_label.image = photo

    history[:] = updated_history
    canvas.yview_moveto(1.0)

# ----------------------- Send Message -----------------------
def send_message():
    user_text = entry.get().strip()
    if not user_text:
        return

    log_to_json("user_input", {"content": user_text})

    add_message("user", user_text)
    entry.delete(0, tk.END)

    history.append({"role": "user", "content": user_text})

    # Create thinking bubble
    bot_frame = Frame(scrollable_frame, bg=BG_DARK)
    bot_frame.pack(anchor="w", fill="x", pady=8, padx=5)

    pic = Label(bot_frame, image=profile_photo, bg=BG_DARK)
    pic.pack(side="left", padx=(0, 10))

    response_label = Label(bot_frame, bg=BG_DARK)
    response_label.pack(side="left")

    thinking_photo = create_bubble_image("Blue Onion is thinking...", BOT_BUBBLE, TEXT_DARK, False)
    response_label.config(image=thinking_photo)
    response_label.image = thinking_photo
    canvas.update_idletasks()
    canvas.yview_moveto(1.0)

    history_copy = [msg.copy() for msg in history]

    threading.Thread(
        target=ai_processing_thread,
        args=(history_copy,),
        daemon=True
    ).start()

    start_queue_checker(response_label)

# ----------------------- Bottom Input Bar -----------------------
bottom = Frame(root, bg=HEADER_BG, height=70)
bottom.pack(fill="x", side="bottom")
bottom.pack_propagate(False)

entry = Entry(bottom, font=("Cascadia Mono", 14), bg="white", fg="black", relief="flat", bd=10)
entry.pack(side="left", fill="both", expand=True, padx=(20, 10), pady=10)

PLACEHOLDER = "Type a message..."
entry.insert(0, PLACEHOLDER)
entry.config(fg="grey")

def on_focus_in(event):
    if entry.get() == PLACEHOLDER:
        entry.delete(0, tk.END)
        entry.config(fg="black")

def on_focus_out(event):
    if not entry.get().strip():
        entry.insert(0, PLACEHOLDER)
        entry.config(fg="grey")

entry.bind("<FocusIn>", on_focus_in)
entry.bind("<FocusOut>", on_focus_out)

send_btn = Button(
    bottom,
    image=send_photo,
    bg=HEADER_BG,
    activebackground=HEADER_BG,
    relief="flat",
    bd=0,
    command=send_message
)
send_btn.pack(side="right", padx=20)

entry.bind("<Return>", lambda e: (send_message() or "break"))

# ----------------------- Start -----------------------
root.mainloop()