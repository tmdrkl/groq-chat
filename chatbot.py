import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from groq import Groq

import rag

HISTORY_FILE = Path(__file__).parent / "history.json"
CONFIG_FILE = Path(__file__).parent / "config.json"
FALLBACK_MODEL = "openai/gpt-oss-120b"
SYSTEM_PROMPT = "Kamu adalah asisten AI yang ramah dan membantu. Jawablah dengan bahasa Indonesia. Gunakan baris baru (newline), jangan gunakan tag HTML seperti <br>."
RAG_SYSTEM_PROMPT = (
    "Kamu adalah asisten yang ahli tentang Void Linux. Jawablah berdasarkan informasi "
    "yang diberikan dalam konteks berikut. Jika jawaban tidak ada di konteks, katakan "
    "bahwa informasi tersebut tidak ditemukan di handbook. Jawab dengan bahasa Indonesia, "
    "sebutkan sumber referensinya. Gunakan baris baru (newline), jangan gunakan tag HTML seperti <br>.\n\n"
    "Konteks dari Void Handbook:\n{context}"
)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print("File history rusak, memulai riwayat baru.", file=sys.stderr)
        return []


def save_history(history: list[dict]) -> None:
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("Error: GROQ_API_KEY tidak ditemukan.", file=sys.stderr)
        print("Set dengan: export GROQ_API_KEY='kunci-anda'", file=sys.stderr)
        sys.exit(1)
    return Groq(api_key=api_key)


def list_models(client: Groq) -> list[str]:
    try:
        return sorted(m.id for m in client.models.list().data)
    except Exception as e:
        print(f"Error mengambil daftar model: {e}", file=sys.stderr)
        return []


def run_chat(client: Groq, history: list[dict], model: str, handbook: "rag.HandbookIndex | None") -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Chat dimulai. Model: {model}")
    rag_note = f" | Handbook Void: {len(handbook.chunks)} chunk" if handbook else " | Handbook Void: tidak aktif"
    print(f"Perintah: exit=keluar, clear=hapus riwayat, /model=ganti model, /models=daftar model, /handbook=query handbook{rag_note}\n")

    while True:
        try:
            user_input = input("Anda: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSampai jumpa!")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "keluar"}:
            print("Sampai jumpa!")
            break
        if user_input.lower() == "clear":
            history.clear()
            save_history(history)
            print("Riwayat chat dihapus.\n")
            continue
        if user_input.lower() == "/models":
            models = list_models(client)
            if models:
                print("Model tersedia:")
                for m in models:
                    print(f"  - {m}")
                print()
            else:
                print("Tidak ada model ditemukan.\n")
            continue
        if user_input.lower().startswith("/model "):
            new_model = user_input.split(maxsplit=1)[1].strip()
            if new_model:
                model = new_model
                save_config({"model": model})
                print(f"Model diganti ke: {model} (tersimpan)\n")
                continue
            print(f"Model saat ini: {model}\n")
            continue
        if user_input.lower().startswith("/handbook"):
            query = user_input.split(maxsplit=1)[1].strip() if " " in user_input else ""
            if not handbook:
                print("Handbook Void tidak tersedia (cek koneksi internet lalu jalankan ulang).\n")
                continue
            if not query:
                print("Gunakan: /handbook <pertanyaan>\n")
                continue
            system_prompt, user_content = build_rag_message(handbook, query, top_k=6, force=True)
            respond(client, model, system_prompt, [{"role": "user", "content": user_content}])
            continue

        system_prompt = SYSTEM_PROMPT
        user_content = user_input
        if handbook:
            results = handbook.search(user_input, top_k=4, min_score=0.08)
            if results:
                system_prompt, user_content = build_rag_message(handbook, user_input, top_k=4, force=True)

        history.append({"role": "user", "content": user_content})

        messages = [{"role": "system", "content": system_prompt}] + history[-20:]

        print("\nBot: ", end="", flush=True)
        try:
            response_text = stream_response(client, model, messages)
            print("\n")
            history.append({"role": "assistant", "content": response_text})
            save_history(history)
        except Exception as e:
            print(f"\nError: {e}\n")
            history.pop()


def build_rag_message(handbook: "rag.HandbookIndex", query: str, top_k: int, force: bool) -> tuple[str, str]:
    results = handbook.search(query, top_k=top_k, min_score=0.0 if force else 0.08)
    context = handbook.format_context(results)
    system_prompt = RAG_SYSTEM_PROMPT.format(context=context)
    user_content = f"Pertanyaan: {query}\n\n(Cari jawaban di Void Handbook di atas.)"
    return system_prompt, user_content


def clean_response(text: str) -> str:
    import re as _re
    return _re.sub(r"<br\s*/?>", "\n", text)


def stream_response(client: Groq, model: str, messages: list[dict]) -> str:
    """Stream respons sambil membersihkan tag <br>, dengan buffer holdback agar
    tag yang terpotong antar chunk tidak bocor ke layar."""
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.7,
        stream=True,
    )

    response_parts = []
    buffer = ""
    for chunk in stream:
        if chunk.choices:
            delta = chunk.choices[0].delta.content
            if delta:
                response_parts.append(delta)
                buffer += delta
                if len(buffer) > 7:
                    safe = buffer[:-5]
                    buffer = buffer[-5:]
                    printed = clean_response(safe)
                    if printed:
                        print(printed, end="", flush=True)

    printed = clean_response(buffer)
    if printed:
        print(printed, end="", flush=True)

    return "".join(response_parts)


def respond(client: Groq, model: str, system_prompt: str, messages: list[dict]) -> None:
    full_messages = [{"role": "system", "content": system_prompt}] + messages
    print("\nBot: ", end="", flush=True)
    try:
        stream_response(client, model, full_messages)
        print("\n")
    except Exception as e:
        print(f"\nError: {e}\n")


def main() -> None:
    load_env(Path(__file__).parent / ".env")
    parser = argparse.ArgumentParser(description="AI chatbot menggunakan Groq")
    parser.add_argument("--model", default=None, help="Model Groq yang digunakan (menimpa model tersimpan)")
    args = parser.parse_args()

    saved = load_config().get("model")
    if args.model:
        model = args.model
        save_config({"model": model})
    elif os.getenv("GROQ_MODEL"):
        model = os.getenv("GROQ_MODEL")
    elif saved:
        model = saved
    else:
        model = FALLBACK_MODEL

    client = get_client()
    history = load_history()

    handbook = None
    if rag.ensure_handbook():
        chunks = rag.load_chunks()
        if chunks:
            handbook = rag.HandbookIndex(chunks)

    run_chat(client, history, model, handbook)


if __name__ == "__main__":
    main()