"""Generate Local AI Model Guide PDF — April 2026"""
from fpdf import FPDF, XPos, YPos

# ── Colors ──────────────────────────────────────────────
BG_DARK    = (24, 24, 32)
BG_CARD    = (34, 34, 48)
BG_TABLE_H = (55, 48, 110)
BG_TABLE_R1= (38, 38, 54)
BG_TABLE_R2= (30, 30, 44)
BG_CODE    = (20, 20, 30)
BG_TIP     = (30, 55, 45)
BG_WARN    = (70, 45, 20)
WHITE      = (240, 240, 245)
GRAY       = (160, 160, 175)
ACCENT     = (130, 100, 255)
ACCENT2    = (80, 200, 170)
GOLD       = (255, 200, 60)
CODE_TXT   = (180, 220, 255)
DIM        = (120, 120, 140)

def ascii_safe(text):
    """Replace Unicode chars that latin-1 can't encode."""
    return (text
        .replace("\u2014", "--")   # em dash
        .replace("\u2013", "-")    # en dash
        .replace("\u2018", "'")    # left single quote
        .replace("\u2019", "'")    # right single quote
        .replace("\u201c", '"')    # left double quote
        .replace("\u201d", '"')    # right double quote
        .replace("\u2026", "...")  # ellipsis
        .replace("\u2192", "->")   # arrow
        .replace("\u2022", "-")    # bullet
        .replace("\u00d7", "x")    # multiplication sign
        .replace("\u2265", ">=")   # >=
        .replace("\u2264", "<=")   # <=
    )

class PDF(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=20)
        self.toc_entries = []

    def header(self):
        if self.page_no() > 1:
            self.set_fill_color(*BG_DARK)
            self.rect(0, 0, 210, 12, "F")
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(*DIM)
            self.set_xy(10, 3)
            self.cell(0, 6, "Local AI Guide  |  April 2026  |  Ribbz", new_x=XPos.RIGHT, new_y=YPos.TOP)
            self.set_xy(-30, 3)
            self.cell(20, 6, f"Page {self.page_no()}", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.ln(4)

    def footer(self):
        pass

    def page_bg(self):
        self.set_fill_color(*BG_DARK)
        self.rect(0, 0, 210, 297, "F")

    def section(self, num, title, level=1):
        """Add a section header and register for TOC."""
        self.toc_entries.append((level, num, title, self.page_no()))
        if self.get_y() > 245:
            self.add_page()
        self.ln(6)
        # accent bar
        self.set_fill_color(*ACCENT)
        self.rect(10, self.get_y(), 3, 10, "F")
        self.set_xy(16, self.get_y())
        size = 16 if level == 1 else 13
        self.set_font("Helvetica", "B", size)
        self.set_text_color(*WHITE)
        label = f"{num}  {title}" if num else title
        self.cell(0, 10, ascii_safe(label), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(3)

    def sub(self, title):
        if self.get_y() > 255:
            self.add_page()
        self.ln(3)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*ACCENT2)
        self.cell(0, 7, ascii_safe(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*WHITE)
        self.ln(1)

    def body(self, text):
        self.set_font("Helvetica", "", 9.5)
        self.set_text_color(*GRAY)
        self.multi_cell(0, 5, ascii_safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def bold_body(self, text):
        self.set_font("Helvetica", "B", 9.5)
        self.set_text_color(*WHITE)
        self.multi_cell(0, 5, ascii_safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def bullet(self, text, indent=14):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*GRAY)
        x = self.get_x()
        self.set_x(x + indent)
        self.set_text_color(*ACCENT2)
        self.cell(5, 5, "-", new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.set_text_color(*GRAY)
        self.multi_cell(170 - indent, 5, ascii_safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(0.5)

    def code(self, text):
        if self.get_y() + 8 > 275:
            self.add_page()
        self.set_fill_color(*BG_CODE)
        self.set_font("Courier", "", 9)
        self.set_text_color(*CODE_TXT)
        lines = text.strip().split("\n")
        h = len(lines) * 5.5 + 6
        y0 = self.get_y()
        self.rect(14, y0, 182, h, "F")
        self.set_xy(18, y0 + 3)
        for line in lines:
            self.cell(0, 5.5, ascii_safe(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_x(18)
        self.set_y(y0 + h + 2)

    def tip(self, text):
        if self.get_y() + 12 > 275:
            self.add_page()
        self.set_fill_color(*BG_TIP)
        y0 = self.get_y()
        self.rect(14, y0, 182, 14, "F")
        self.set_xy(18, y0 + 2)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*ACCENT2)
        self.cell(8, 5, "TIP", new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.set_font("Helvetica", "", 8.5)
        self.set_text_color(*WHITE)
        self.cell(160, 5, ascii_safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_y(y0 + 16)

    def warn(self, text):
        if self.get_y() + 12 > 275:
            self.add_page()
        self.set_fill_color(*BG_WARN)
        y0 = self.get_y()
        self.rect(14, y0, 182, 14, "F")
        self.set_xy(18, y0 + 2)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*GOLD)
        self.cell(8, 5, "!", new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.set_font("Helvetica", "", 8.5)
        self.set_text_color(*WHITE)
        self.cell(160, 5, ascii_safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_y(y0 + 16)

    def table(self, headers, rows, col_widths=None):
        if not col_widths:
            w = 182 / len(headers)
            col_widths = [w] * len(headers)
        # header
        if self.get_y() + 10 > 270:
            self.add_page()
        self.set_fill_color(*BG_TABLE_H)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*WHITE)
        x0 = 14
        self.set_x(x0)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 8, ascii_safe(h), border=0, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.ln(8)
        # rows
        self.set_font("Helvetica", "", 8)
        for ri, row in enumerate(rows):
            if self.get_y() + 7 > 275:
                self.add_page()
                # re-draw header on new page
                self.set_fill_color(*BG_TABLE_H)
                self.set_font("Helvetica", "B", 8)
                self.set_text_color(*WHITE)
                self.set_x(x0)
                for i, h in enumerate(headers):
                    self.cell(col_widths[i], 8, ascii_safe(h), border=0, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
                self.ln(8)
                self.set_font("Helvetica", "", 8)
            bg = BG_TABLE_R1 if ri % 2 == 0 else BG_TABLE_R2
            self.set_fill_color(*bg)
            self.set_text_color(*GRAY)
            self.set_x(x0)
            for i, val in enumerate(row):
                self.cell(col_widths[i], 7, ascii_safe(str(val)), border=0, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
            self.ln(7)
        self.ln(3)


def build():
    pdf = PDF()
    pdf.set_margins(10, 10, 10)

    # ══════════════════════════════════════════════════
    # COVER PAGE
    # ══════════════════════════════════════════════════
    pdf.add_page()
    pdf.page_bg()
    # big accent block
    pdf.set_fill_color(*ACCENT)
    pdf.rect(0, 80, 210, 4, "F")
    pdf.set_fill_color(40, 30, 80)
    pdf.rect(0, 84, 210, 80, "F")

    pdf.set_xy(10, 35)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(*DIM)
    pdf.cell(0, 6, "RIBBZ  //  APRIL 2026", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_xy(10, 92)
    pdf.set_font("Helvetica", "B", 32)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 14, "RUN AI LOCALLY", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_xy(10, 110)
    pdf.set_font("Helvetica", "", 14)
    pdf.set_text_color(*ACCENT2)
    pdf.cell(0, 8, "The Complete Guide to Free Local AI Models on Windows 10", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_xy(10, 130)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*GRAY)
    pdf.cell(0, 6, ascii_safe("Gemma 4  |  Qwen 3.5  |  Phi-4  |  Llama 4  |  DeepSeek  |  Mistral"), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, ascii_safe("Ollama  |  LM Studio  |  llama.cpp  |  KoboldCpp"), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_xy(10, 175)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(*DIM)
    pdf.cell(0, 5, "Hardware requirements, step-by-step setup, benchmarks, and recommendations", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 5, "for every GPU tier from CPU-only to RTX 4090.", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # ══════════════════════════════════════════════════
    # TABLE OF CONTENTS
    # ══════════════════════════════════════════════════
    pdf.add_page()
    pdf.page_bg()
    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 12, "TABLE OF CONTENTS", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_fill_color(*ACCENT)
    pdf.rect(10, pdf.get_y(), 60, 2, "F")
    pdf.ln(8)

    toc_items = [
        ("1", "Model Rankings — The Best Right Now", ""),
        ("",  "   Tier 1: Best-in-Class", ""),
        ("",  "   Tier 2: Strong Contenders", ""),
        ("",  "   Bottom Line Summary", ""),
        ("2", "Hardware Requirements by Model", ""),
        ("3", "Software Runners", ""),
        ("",  "   Ollama  /  LM Studio  /  llama.cpp  /  KoboldCpp", ""),
        ("4", "Step-by-Step Setup: Gemma 4 via Ollama", ""),
        ("",  "   Install  /  Pull Model  /  Chat  /  API  /  Web UI", ""),
        ("5", "Recommendations by Hardware Tier", ""),
        ("",  "   8GB  /  12GB  /  16GB+  /  24GB  /  CPU-Only", ""),
        ("6", "Quick Reference Card", ""),
    ]
    for num, title, _ in toc_items:
        if num:
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*ACCENT)
            pdf.cell(10, 7, num, new_x=XPos.RIGHT, new_y=YPos.TOP)
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*WHITE)
            pdf.cell(0, 7, ascii_safe(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*DIM)
            pdf.cell(10, 6, "", new_x=XPos.RIGHT, new_y=YPos.TOP)
            pdf.cell(0, 6, ascii_safe(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # ══════════════════════════════════════════════════
    # SECTION 1 — MODEL RANKINGS
    # ══════════════════════════════════════════════════
    pdf.add_page()
    pdf.page_bg()
    pdf.section("1", "MODEL RANKINGS")
    pdf.body("The landscape as of April 2026. Your instinct about Gemma was right — but it's Gemma 4 (released April 2, 2026), not Gemma 3. It's a generational leap.")

    pdf.sub("TIER 1 — Best-in-Class (Run These)")

    pdf.bold_body("#1 — Gemma 4 31B Dense  (Google  |  Apache 2.0)")
    pdf.bullet("Currently #3 on LM Arena among ALL open models")
    pdf.bullet("AIME 2026: 20.8% -> 89.2%  |  GPQA Diamond: 42.4% -> 84.3%")
    pdf.bullet("Competitive coding: 29.1% -> 80.0%  |  Agentic tool use: 6.6% -> 86.4%")
    pdf.bullet("Multimodal: images, video (up to 60s), text — out of the box")
    pdf.bullet("Apache 2.0 = fully free including commercial use")
    pdf.bullet("Needs ~18-20 GB VRAM at Q4 quantization. Best fit: 24 GB cards (RTX 3090/4090)")
    pdf.ln(2)

    pdf.bold_body("#1B — Gemma 4 26B MoE  (Google  |  Apache 2.0)  [SWEET SPOT]")
    pdf.bullet("Mixture-of-Experts: only 3.8B params active per token = much faster inference")
    pdf.bullet("Nearly identical benchmark scores to the 31B Dense on most tasks")
    pdf.bullet("Needs ~8-10 GB VRAM at Q4 — fits on 12 GB cards comfortably")
    pdf.tip("For most Windows 10 users with a mid-range GPU, the 26B MoE is THE pick.")
    pdf.ln(2)

    pdf.bold_body("#2 — Qwen 3.5  (Alibaba  |  Apache 2.0)")
    pdf.bullet("Qwen 3.5 9B matches or beats Llama 3.3 70B on reasoning — 85% less VRAM")
    pdf.bullet("14B variant is the best balance of quality vs consumer hardware")
    pdf.bullet("Has a toggleable 'thinking mode' (chain-of-thought) per prompt")
    pdf.bullet("Best multilingual support: Chinese, Japanese, Korean, Arabic, 15+ languages")
    pdf.bullet("VRAM: ~4.6 GB (8B) or ~8.3 GB (14B) at Q4")
    pdf.ln(2)

    pdf.bold_body("#3 — Phi-4  (Microsoft  |  MIT License)")
    pdf.bullet("Phi-4-Mini (3.8B): 83.7% ARC-C, 88.6% GSM8K — absurdly strong for its size")
    pdf.bullet("Phi-4 14B: leads MATH benchmark at 80.4%, best reasoning-per-GB of any model")
    pdf.bullet("MIT license. Runs on tight hardware.")
    pdf.bullet("VRAM: ~2.3 GB (Mini) or ~8.5 GB (14B) at Q4")

    pdf.sub("TIER 2 — Strong Contenders")

    pdf.bold_body("#4 — Llama 4 Scout  (Meta  |  Llama 4 License)")
    pdf.bullet("MoE architecture, 109B total parameters")
    pdf.bullet("Needs ~61 GB at Q4 — does NOT fit on consumer desktop GPUs")
    pdf.bullet("Requires multi-GPU or Apple Silicon M4 Max with 128 GB unified memory")
    pdf.warn("Skip for Windows 10 unless you have extreme hardware (multi-GPU).")
    pdf.ln(2)

    pdf.bold_body("#5 — Mistral Small 3  (7B)  (Mistral  |  Apache 2.0)")
    pdf.bullet("Fastest model in this tier — 4.1 GB on disk, excellent for speed-critical tasks")
    pdf.bullet("Not the smartest anymore, but unbeatable for sub-second latency")
    pdf.bullet("VRAM: ~4-5 GB at Q4")
    pdf.ln(2)

    pdf.bold_body("#6 — DeepSeek R2 / V3  (DeepSeek)")
    pdf.bullet("DeepSeek R2 (March 2026): exceptional reasoning, but FP8 = 600-700 GB. Data center only.")
    pdf.bullet("R1 Distill variants (7B, 14B) are the locally-runnable versions — excellent for reasoning")
    pdf.tip("For Windows 10: use DeepSeek-R1 distills via Ollama, not the full R2.")

    pdf.sub("Bottom Line")
    pdf.body("Most Windows 10 users: Gemma 4 26B MoE (12 GB+ VRAM) or Qwen 3.5 14B (8 GB+ VRAM). If you have a 24 GB card, go Gemma 4 31B Dense. Tight hardware? Phi-4-Mini is the play.")

    # ══════════════════════════════════════════════════
    # SECTION 2 — HARDWARE REQUIREMENTS
    # ══════════════════════════════════════════════════
    pdf.add_page()
    pdf.page_bg()
    pdf.section("2", "HARDWARE REQUIREMENTS")

    pdf.table(
        ["Model", "Q4 VRAM", "Q8 VRAM", "FP16 VRAM"],
        [
            ["Phi-4-Mini (3.8B)",       "~2.3 GB",   "~4 GB",    "~7.6 GB"],
            ["Mistral 7B",              "~4.1 GB",   "~7.5 GB",  "~14 GB"],
            ["Qwen 3.5 8B",             "~4.6 GB",   "~8.5 GB",  "~16 GB"],
            ["Gemma 4 26B MoE",         "~8-10 GB",  "~16 GB",   "~52 GB"],
            ["Qwen 3.5 14B",            "~8.3 GB",   "~15 GB",   "~28 GB"],
            ["Phi-4 14B",               "~8.5 GB",   "~15 GB",   "~28 GB"],
            ["Gemma 4 31B Dense",       "~18-20 GB", "~32 GB",   "~62 GB"],
            ["Llama 4 Scout 109B MoE",  "~61 GB",    "too large", "too large"],
            ["DeepSeek R2 (full)",      "~600+ GB",  "---",       "---"],
        ],
        [60, 35, 35, 52]
    )

    pdf.sub("System Requirements")
    pdf.bullet("RAM: always have at least 2x your VRAM in system RAM. 16 GB minimum; 32 GB comfortable for 13B+ models")
    pdf.bullet("Any layers that don't fit in VRAM spill to system RAM at ~10x slower speed")
    pdf.bullet("CPU minimum: 8-core (Intel i7 12th gen / AMD Ryzen 5 5600X or newer)")
    pdf.bullet("Storage: models are 4-20 GB each. SSD recommended for fast loading.")

    pdf.sub("What is Q4 / Q8 / FP16?")
    pdf.bullet("FP16 = full precision (largest, highest quality, most VRAM)")
    pdf.bullet("Q8 = 8-bit quantization (~50% size reduction, negligible quality loss)")
    pdf.bullet("Q4 = 4-bit quantization (~75% size reduction, minor quality loss, best for consumer GPUs)")
    pdf.tip("Q4_K_M is the sweet spot — best quality-to-size ratio. Use this unless you have VRAM to spare.")

    # ══════════════════════════════════════════════════
    # SECTION 3 — SOFTWARE RUNNERS
    # ══════════════════════════════════════════════════
    pdf.add_page()
    pdf.page_bg()
    pdf.section("3", "SOFTWARE RUNNERS")
    pdf.body("You need a 'runner' — software that loads the model weights and runs inference on your GPU/CPU. Four main options:")

    pdf.sub("Option A — Ollama  [Recommended]")
    pdf.bullet("52 million monthly downloads in Q1 2026 — the de facto standard")
    pdf.bullet("One-click Windows installer, auto GPU detection")
    pdf.bullet("OpenAI-compatible API at localhost:11434")
    pdf.bullet("Dead simple: 'ollama run gemma4:26b' and you're chatting")
    pdf.bullet("Best for: developers, API integration, power users")
    pdf.bullet("Download: ollama.com/download/windows")
    pdf.ln(2)

    pdf.sub("Option B — LM Studio  [Best GUI]")
    pdf.bullet("Full desktop app with a ChatGPT-like interface + model browser")
    pdf.bullet("No command line needed, built-in Hugging Face model downloader")
    pdf.bullet("Visual config: context length, temperature, top-p, etc.")
    pdf.bullet("Also serves an OpenAI-compatible API")
    pdf.bullet("Best for: non-technical users, exploring models, polished UI experience")
    pdf.bullet("Download: lmstudio.ai")
    pdf.ln(2)

    pdf.sub("Option C — llama.cpp  [Maximum Control]")
    pdf.bullet("The raw C++ inference engine that Ollama and KoboldCpp both build on")
    pdf.bullet("Maximum control, fastest raw performance")
    pdf.bullet("Supports every GGUF model, Vulkan backend for AMD GPUs")
    pdf.bullet("Command-line only, requires prebuilt binaries or compilation")
    pdf.bullet("Best for: developers who want fine-grained control, benchmarking")
    pdf.bullet("Download: github.com/ggml-org/llama.cpp/releases")
    pdf.ln(2)

    pdf.sub("Option D — KoboldCpp  [Zero Install]")
    pdf.bullet("Single .exe file, truly zero install — just download and run")
    pdf.bullet("Built on llama.cpp with a full web UI")
    pdf.bullet("CUDA and Vulkan GPU support (--usecuda / --usevulkan flags)")
    pdf.bullet("Works on AMD GPUs via Vulkan — one of the best AMD options")
    pdf.bullet("Best for: users who want GUI without installing anything, AMD GPU users")
    pdf.bullet("Download: github.com/LostRuins/koboldcpp")

    # ══════════════════════════════════════════════════
    # SECTION 4 — STEP BY STEP SETUP
    # ══════════════════════════════════════════════════
    pdf.add_page()
    pdf.page_bg()
    pdf.section("4", "STEP-BY-STEP: GEMMA 4 VIA OLLAMA")
    pdf.body("The recommended setup for 12 GB+ VRAM. Best quality-to-hardware ratio in April 2026.")

    pdf.sub("Step 1: Install Ollama")
    pdf.bullet("Go to ollama.com/download/windows")
    pdf.bullet("Download OllamaSetup.exe and double-click — takes under 2 minutes")
    pdf.bullet("Installs to %LOCALAPPDATA%\\Programs\\Ollama, adds to PATH, starts background server")
    pdf.bullet("No reboot needed")
    pdf.ln(1)
    pdf.body("Verify install:")
    pdf.code("ollama --version")

    pdf.sub("Step 2: Pull the Model")
    pdf.body("For 12 GB VRAM (RTX 3060 12GB, RTX 4070):")
    pdf.code("ollama pull gemma4:26b")
    pdf.body("For 24 GB VRAM (RTX 3090, RTX 4090):")
    pdf.code("ollama pull gemma4:31b")
    pdf.body("Check your downloaded models:")
    pdf.code("ollama list")
    pdf.tip("Downloads are ~16 GB. Ollama handles chunked downloads and resumes if interrupted.")

    pdf.sub("Step 3: Chat (Interactive Mode)")
    pdf.code("ollama run gemma4:26b")
    pdf.body("Type your prompt and hit Enter. Type /bye to exit. That's it.")

    pdf.sub("Step 4: Use as a Local API")
    pdf.body("Ollama auto-starts a server at http://localhost:11434. It's 100% OpenAI-compatible.")
    pdf.ln(1)
    pdf.body("Test the server is running:")
    pdf.code("curl http://localhost:11434/api/tags")
    pdf.ln(1)
    pdf.body("Send a prompt via API (OpenAI format):")
    pdf.code('curl http://localhost:11434/v1/chat/completions \\\n  -H "Content-Type: application/json" \\\n  -d \'{"model":"gemma4:26b","messages":[{"role":"user",\n       "content":"Hello, who are you?"}]}\'')

    pdf.add_page()
    pdf.page_bg()
    pdf.body("Use with the OpenAI Python SDK:")
    pdf.code('from openai import OpenAI\n\nclient = OpenAI(\n    base_url="http://localhost:11434/v1",\n    api_key="ollama"  # required but ignored\n)\n\nresponse = client.chat.completions.create(\n    model="gemma4:26b",\n    messages=[{"role": "user",\n              "content": "Explain mixture of experts."}]\n)\nprint(response.choices[0].message.content)')
    pdf.tip("Any app that accepts an OpenAI base_url can point to localhost:11434/v1 and just work.")

    pdf.sub("Step 5: Optional — Web UI (ChatGPT-like)")
    pdf.body("For a browser interface on top of Ollama, install Open WebUI via Docker:")
    pdf.code("docker run -d -p 3000:80 \\\n  --add-host=host.docker.internal:host-gateway \\\n  -v open-webui:/app/backend/data \\\n  --name open-webui \\\n  ghcr.io/open-webui/open-webui:main")
    pdf.body("Then open http://localhost:3000 in any browser.")
    pdf.body("Alternative: just use LM Studio instead for the same experience without Docker.")

    # ══════════════════════════════════════════════════
    # SECTION 5 — HARDWARE TIER RECOMMENDATIONS
    # ══════════════════════════════════════════════════
    pdf.add_page()
    pdf.page_bg()
    pdf.section("5", "RECOMMENDATIONS BY HARDWARE")

    pdf.sub("8 GB VRAM  (RTX 3060 8GB / RTX 3070 / RTX 4060)")
    pdf.table(
        ["Model", "VRAM (Q4)", "Command"],
        [
            ["Qwen 3.5 8B  [PICK]", "~4.6 GB", "ollama pull qwen3.5:8b"],
            ["Phi-4-Mini 3.8B",      "~2.3 GB", "ollama pull phi4-mini"],
            ["Mistral 7B",           "~4.1 GB", "ollama pull mistral:7b"],
            ["Gemma 4 E4B",          "~3 GB",   "ollama pull gemma4:4b"],
        ],
        [60, 35, 87]
    )

    pdf.sub("12 GB VRAM  (RTX 3060 12GB / RTX 4070 / Arc B580)")
    pdf.table(
        ["Model", "VRAM (Q4)", "Command"],
        [
            ["Gemma 4 26B MoE [PICK]", "~8-10 GB", "ollama pull gemma4:26b"],
            ["Qwen 3.5 14B",           "~8.3 GB",  "ollama pull qwen3.5:14b"],
            ["Phi-4 14B",              "~8.5 GB",  "ollama pull phi4:14b"],
            ["DeepSeek R1 14B",        "~8.5 GB",  "ollama pull deepseek-r1:14b"],
        ],
        [60, 35, 87]
    )

    pdf.sub("16-20 GB VRAM  (RTX 4070 Ti / RTX 4080 / RX 7900 XTX)")
    pdf.table(
        ["Model", "VRAM (Q4)", "Command"],
        [
            ["Gemma 4 26B MoE [PICK]", "~8-10 GB",  "ollama pull gemma4:26b"],
            ["Qwen 3.5 30B MoE",       "~16.8 GB",  "ollama pull qwen3.5:30b"],
            ["Qwen 3 Coder 30B",       "~18 GB",    "ollama pull qwen3-coder:30b"],
        ],
        [60, 35, 87]
    )

    pdf.sub("24 GB VRAM  (RTX 3090 / RTX 4090 / RTX 5090)")
    pdf.table(
        ["Model", "VRAM (Q4)", "Command"],
        [
            ["Gemma 4 31B Dense [PICK]", "~18-20 GB", "ollama pull gemma4:31b"],
            ["Qwen 3.5 32B",             "~19 GB",    "ollama pull qwen3.5:32b"],
            ["DeepSeek R1 32B",          "~19 GB",    "ollama pull deepseek-r1:32b"],
        ],
        [60, 35, 87]
    )

    pdf.sub("CPU-Only  (No discrete GPU)")
    pdf.body("Running on CPU is slow (3-10 tokens/sec) but fully functional for casual use. Ollama auto-detects no GPU and falls back to CPU — zero config needed.")
    pdf.table(
        ["Model", "RAM Needed", "Command"],
        [
            ["Phi-4-Mini 3.8B [PICK]", "8 GB",  "ollama pull phi4-mini"],
            ["Gemma 4 E2B",             "4 GB",  "ollama pull gemma4:2b"],
            ["Mistral 7B (Q4)",         "16 GB", "ollama pull mistral:7b"],
        ],
        [60, 35, 87]
    )

    # ══════════════════════════════════════════════════
    # SECTION 6 — QUICK REFERENCE
    # ══════════════════════════════════════════════════
    pdf.add_page()
    pdf.page_bg()
    pdf.section("6", "QUICK REFERENCE CARD")

    pdf.sub("Best Model by Goal")
    pdf.table(
        ["Goal", "Model", "Ollama Command"],
        [
            ["Best all-around (12GB+)",   "Gemma 4 26B MoE",     "ollama run gemma4:26b"],
            ["Best all-around (24GB)",    "Gemma 4 31B Dense",   "ollama run gemma4:31b"],
            ["Best for coding",           "Qwen 3 Coder 30B",   "ollama run qwen3-coder:30b"],
            ["Best reasoning",            "DeepSeek R1 14B",     "ollama run deepseek-r1:14b"],
            ["Best small / fast",         "Phi-4-Mini",          "ollama run phi4-mini"],
            ["CPU-only",                  "Gemma 4 E2B",         "ollama run gemma4:2b"],
            ["Best multilingual",         "Qwen 3.5 14B",       "ollama run qwen3.5:14b"],
        ],
        [52, 48, 82]
    )

    pdf.sub("Essential Ollama Commands")
    pdf.code("ollama list                    # see downloaded models\nollama pull <model>             # download a model\nollama run <model>              # interactive chat\nollama rm <model>               # delete a model\nollama show <model>             # model details + params\nollama ps                       # running models + VRAM usage\nollama serve                    # start server manually")

    pdf.sub("API Endpoints")
    pdf.code("GET  http://localhost:11434/api/tags          # list models\nPOST http://localhost:11434/v1/chat/completions  # OpenAI compat\nPOST http://localhost:11434/api/generate         # native API\nPOST http://localhost:11434/api/embeddings       # embeddings")

    pdf.sub("Key Downloads")
    pdf.bullet("Ollama: ollama.com/download/windows")
    pdf.bullet("LM Studio: lmstudio.ai")
    pdf.bullet("KoboldCpp: github.com/LostRuins/koboldcpp")
    pdf.bullet("llama.cpp: github.com/ggml-org/llama.cpp/releases")
    pdf.bullet("Open WebUI (Docker): github.com/open-webui/open-webui")

    pdf.ln(8)
    pdf.set_fill_color(*ACCENT)
    pdf.rect(10, pdf.get_y(), 190, 2, "F")
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(*DIM)
    pdf.cell(0, 5, "Generated April 2026  |  Sources: Google DeepMind, Alibaba Qwen, Microsoft Research,", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 5, "Meta AI, Mistral, DeepSeek, LM Arena, Ollama, arXiv papers", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # ── Save ────────────────────────────────────────
    out = "~/Desktop/Local_AI_Guide_April_2026.pdf"
    pdf.output(out)
    print(f"PDF saved: {out}")
    print(f"Pages: {pdf.pages_count}")

if __name__ == "__main__":
    build()
