"""End-to-end tests using synthetic EPUB data, SudachiPy, Jamdict, and FastAPI TestClient."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, dictionary, llamacpp_manager, llm
from app.epub_parser import parse_epub, read_epub_asset
from app.main import app
from app.manga_parser import extract_manga_toc, parse_manga
from app.services import books_service, grammar_service, lookup_service, manga_ocr_service, manga_service, quiz_service
from app.tokenizer import (
    chunk_into_bunsetsu,
    extract_particle_quiz,
    tokenize_chapter_html,
    tokenize_plain_text,
)

# Minimal valid 1x1 transparent PNG
_SAMPLE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _build_sample_epub(epub_path: Path, title: str = "サンプル小説 第一巻") -> Path:
    """Create a synthetic Japanese EPUB 2 file with sparse NCX, SVG cover, ruby, and illustrations."""
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

    content_opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uuid_id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{title}</dc:title>
    <dc:creator opf:role="aut">テスト著者</dc:creator>
    <dc:language>ja</dc:language>
    <meta name="cover" content="cover_img"/>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="cover_img" href="cover.png" media-type="image/png"/>
    <item id="illust1" href="OEBPS/illust1.png" media-type="image/png"/>
    <item id="titlepage" href="titlepage.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch1" href="OEBPS/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="img1" href="OEBPS/img1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch1b" href="OEBPS/ch1b.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="OEBPS/ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="colophon" href="OEBPS/colophon.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="titlepage"/>
    <itemref idref="ch1"/>
    <itemref idref="img1"/>
    <itemref idref="ch1b"/>
    <itemref idref="ch2"/>
    <itemref idref="colophon"/>
  </spine>
</package>"""

    # Intentionally sparse NCX (only Cover & Colophon) to verify heading-based grouping
    toc_ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <docTitle><text>{title}</text></docTitle>
  <navMap>
    <navPoint id="n0" playOrder="0"><navLabel><text>表紙</text></navLabel><content src="titlepage.xhtml"/></navPoint>
    <navPoint id="n1" playOrder="1"><navLabel><text>奥付</text></navLabel><content src="OEBPS/colophon.xhtml"/></navPoint>
  </navMap>
</ncx>"""

    titlepage = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja">
  <head><title>Cover</title></head>
  <body>
    <div><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 100">
      <image width="100" height="100" xlink:href="cover.png"/>
    </svg></div>
  </body>
</html>"""

    ch1 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja">
  <head><title>Chapter 1</title></head>
  <body>
    <h2>１</h2>
    <p>　<ruby><rb>駅</rb><rt>えき</rt><rb>前</rb><rt>まえ</rt></ruby>にはお<ruby><rb>店</rb><rt>みせ</rt></ruby>が<ruby><rb>立</rb><rt>た</rt></ruby>ち<ruby><rb>並</rb><rt>なら</rt></ruby>ぶ<ruby><rb>商</rb><rt>しょう</rt><rb>店</rb><rt>てん</rt><rb>街</rb><rt>がい</rt></ruby>がある。</p>
    <p>　<ruby><rb>青</rb><rt>あお</rt></ruby>い<ruby><rb>空</rb><rt>そら</rt></ruby>の<ruby><rb>下</rb><rt>した</rt></ruby>で<ruby><rb>透</rb><rt>す</rt></ruby>き<ruby><rb>通</rb><rt>とお</rt></ruby>った<ruby><rb>水</rb><rt>みず</rt></ruby>が<ruby><rb>静</rb><rt>しず</rt></ruby>かに<ruby><rb>流</rb><rt>なが</rt></ruby>れている。</p>
    <p>　<ruby><rb>田舎</rb><rt>いなか</rt></ruby>の<ruby><rb>朝</rb><rt>あさ</rt></ruby>には、いつも<ruby><rb>小</rb><rt>こ</rt><rb>鳥</rb><rt>とり</rt></ruby>の<ruby><rb>声</rb><rt>こえ</rt></ruby>が<ruby><rb>聞</rb><rt>き</rt></ruby>こえる。</p>
  </body>
</html>"""

    img1 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja">
  <head><title>Illustration</title></head>
  <body><div><img src="illust1.png" alt=""/></div></body>
</html>"""

    ch1b = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja">
  <head><title>Chapter 1 cont.</title></head>
  <body>
    <p>「<ruby><rb>今</rb><rt>きょう</rt><rb>日</rb><rt>きょう</rt></ruby>はいい<ruby><rb>天</rb><rt>てん</rt><rb>気</rb><rt>き</rt></ruby>だね！」</p>
    <p>　<ruby><rb>歩</rb><rt>ある</rt></ruby>きながら<ruby><rb>友</rb><rt>とも</rt><rb>達</rb><rt>だち</rt></ruby>と<ruby><rb>笑</rb><rt>わら</rt></ruby>いあった。</p>
  </body>
</html>"""

    ch2 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja">
  <head><title>Chapter 2</title></head>
  <body>
    <h2>２</h2>
    <p>　<ruby><rb>午</rb><rt>ご</rt><rb>後</rb><rt>ご</rt></ruby>になって、<ruby><rb>風</rb><rt>かぜ</rt></ruby>が<ruby><rb>少</rb><rt>すこ</rt></ruby>し<ruby><rb>冷</rb><rt>つめ</rt></ruby>たくなった。</p>
  </body>
</html>"""

    colophon = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja">
  <head><title>Colophon</title></head>
  <body><p>テスト刊行</p></body>
</html>"""

    epub_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("content.opf", content_opf)
        zf.writestr("toc.ncx", toc_ncx)
        zf.writestr("cover.png", _SAMPLE_PNG)
        zf.writestr("OEBPS/illust1.png", _SAMPLE_PNG)
        zf.writestr("titlepage.xhtml", titlepage)
        zf.writestr("OEBPS/ch1.xhtml", ch1)
        zf.writestr("OEBPS/img1.xhtml", img1)
        zf.writestr("OEBPS/ch1b.xhtml", ch1b)
        zf.writestr("OEBPS/ch2.xhtml", ch2)
        zf.writestr("OEBPS/colophon.xhtml", colophon)
    return epub_path


@pytest.fixture()
def temp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    books_dir = tmp_path / "books"
    books_dir.mkdir()
    _build_sample_epub(books_dir / "sample_vol1.epub", "サンプル小説 第一巻")
    _build_sample_epub(books_dir / "sample_vol2.epub", "サンプル小説 第二巻")

    manga_dir = tmp_path / "manga"
    manga_dir.mkdir()
    test_db = tmp_path / "test_app.db"
    monkeypatch.setattr(config, "BOOKS_DIR", books_dir)
    monkeypatch.setattr(config, "MANGA_DIR", manga_dir)
    db.set_db_path(test_db)
    db.init_db()
    yield books_dir, test_db
    db.set_db_path(None)


def test_parse_synthetic_epub_and_assets(tmp_path: Path):
    epub = _build_sample_epub(tmp_path / "sample.epub")
    parsed = parse_epub(epub)
    titles = [c.title for c in parsed.chapters]

    # Front matter + Chapter 1 (merged across illustration) + Chapter 2 + Colophon
    assert titles == ["表紙・まえがき", "１", "２", "奥付"]
    ch1 = parsed.chapters[1]
    assert len(ch1.spine_hrefs) == 3
    assert "/api/books/__BOOK_ID__/assets/OEBPS/illust1.png" in ch1.raw_html

    assert parsed.cover_href == "cover.png"
    cover_asset = read_epub_asset(epub, parsed.cover_href)
    assert cover_asset is not None
    cover_bytes, cover_mime = cover_asset
    assert cover_bytes == _SAMPLE_PNG
    assert cover_mime == "image/png"
    assert read_epub_asset(epub, "../../pyproject.toml") is None


def test_ruby_aware_tokenization_preserves_furigana(tmp_path: Path):
    epub = _build_sample_epub(tmp_path / "sample.epub")
    parsed = parse_epub(epub)
    ch1 = next(c for c in parsed.chapters if c.title == "１")

    tokenized_html, sentences, char_count = tokenize_chapter_html(ch1.raw_html, ch1.chapter_index)
    assert char_count > 50
    assert len(sentences) >= 5

    soup = BeautifulSoup(tokenized_html, "lxml")
    toks = soup.select(".tok")
    assert len(toks) > 15

    by_surface = {}
    for tok in toks:
        by_surface.setdefault(tok.get("data-surface"), []).append(tok)

    for expected_word, expected_lemma in (
        ("立ち並ぶ", "立ち並ぶ"),
        ("商店街", "商店街"),
        ("田舎", "田舎"),
    ):
        matches = by_surface.get(expected_word, [])
        assert matches, f"Expected token {expected_word!r} in Chapter 1"
        first = matches[0]
        assert first.get("data-lemma") == expected_lemma
        assert first.get("data-reading")
        assert first.find("ruby") is not None
        assert first.find("rt") is not None

    ask_btns = soup.select(".sent .sent-ask-btn")
    assert len(ask_btns) == len(sentences)
    assert "?" not in sentences[0]["text"]


def test_offline_jamdict_multithreaded():
    words = ["食べる", "立ち並ぶ", "透き通る", "商店街"]

    def worker(w: str):
        return dictionary.lookup_english(lemma=w, surface=w)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, words))

    for word, res in zip(words, results):
        assert not res.get("missing_db"), res.get("error")
        assert res["entries"], f"Expected JMdict entry for {word}"
        assert res["entries"][0]["senses"][0]["glosses"]


def test_api_end_to_end(temp_env, monkeypatch: pytest.MonkeyPatch):
    jj_calls = 0

    async def fake_generate_text(
        *, settings, system_prompt, messages, expect_json=False, json_schema=None
    ):
        nonlocal jj_calls
        if expect_json:
            jj_calls += 1
            return (
                json.dumps(
                    {
                        "simple_definition": "たくさんの店がならんで立っていること。",
                        "part_of_speech": "動詞",
                        "context_note": "町の通りに店がいっぱいある様子を表しています。",
                        "examples": [
                            {"jp": "駅前にビルが立ち並ぶ。", "reading": "えきまえ・たちならぶ"},
                            {"jp": "古い家が立ち並んでいる。", "reading": "ふるい・いえ"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                "ollama",
                "qwen3:8b",
            )
        return (
            "- **〜てしまう** expresses completion or regret.\n- Context shows an ordinary street scene.",
            "ollama",
            "qwen3:8b",
        )

    monkeypatch.setattr(llm, "generate_text", fake_generate_text)

    with TestClient(app) as client:
        # 1. Library listing & chapter fetch
        books_resp = client.get("/api/books")
        assert books_resp.status_code == 200
        books = books_resp.json()["books"]
        assert len(books) == 2
        book1 = books[0]
        ch1_idx = next(c["chapter_index"] for c in book1["chapters"] if c["title"] == "１")

        ch_resp = client.get(f"/api/books/{book1['id']}/chapters/{ch1_idx}")
        assert ch_resp.status_code == 200
        ch_data = ch_resp.json()
        assert ch_data["title"] == "１"
        assert 'class="tok"' in ch_data["html"]
        assert config.BOOK_ID_PLACEHOLDER not in ch_data["html"]

        # 2. Cover asset & path traversal defense
        assert book1["cover_url"]
        cover_resp = client.get(book1["cover_url"])
        assert cover_resp.status_code == 200
        assert cover_resp.headers["content-type"].startswith("image/")

        trav_resp = client.get(f"/api/books/{book1['id']}/assets/%2e%2e/%2e%2e/pyproject.toml")
        assert trav_resp.status_code in (400, 404)
        missing_resp = client.get(f"/api/books/{book1['id']}/assets/does-not-exist.png")
        assert missing_resp.status_code == 404

        # 3. Step 1 J-J lookup caching
        jj_payload = {
            "lemma": "立ち並ぶ",
            "reading": "たちならぶ",
            "surface": "立ち並ぶ",
            "pos": "動詞",
            "sentence_text": "駅前にはお店が立ち並ぶ商店街がある。",
            "book_id": book1["id"],
            "chapter_index": ch1_idx,
            "sent_id": f"c{ch1_idx}_s0",
        }
        r1 = client.post("/api/lookup/jj", json=jj_payload)
        assert r1.status_code == 200
        assert r1.json()["cached"] is False
        assert r1.json()["jj"]["simple_definition"]

        r2 = client.post("/api/lookup/jj", json=jj_payload)
        assert r2.status_code == 200
        assert r2.json()["cached"] is True
        assert jj_calls == 1

        # 4. Step 2 offline English lookup + trouble tracking
        en_resp = client.post(
            "/api/lookup/en",
            json={
                "lemma": "立ち並ぶ",
                "norm_form": "立ち並ぶ",
                "surface": "立ち並ぶ",
                "reading": "たちならぶ",
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s0",
            },
        )
        assert en_resp.status_code == 200
        assert en_resp.json()["entries"]

        trouble_resp = client.get("/api/trouble")
        assert trouble_resp.status_code == 200
        trouble_items = trouble_resp.json()["items"]
        word_item = next(i for i in trouble_items if i["item_key"] == "立ち並ぶ|たちならぶ")
        assert word_item["jj_views"] >= 2
        assert word_item["en_reveals"] == 1
        assert word_item["trouble_score"] >= 3.0

        # 5. Grammar Q&A thread + follow-up
        g1 = client.post(
            "/api/grammar/ask",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s0",
                "target_text": "立ち並ぶ",
                "question": "",
            },
        )
        assert g1.status_code == 200
        thread = g1.json()["thread"]
        assert len(thread["messages"]) == 2

        g2 = client.post(
            "/api/grammar/ask",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s0",
                "target_text": "立ち並ぶ",
                "question": "Can this also be used for mountains?",
            },
        )
        assert g2.status_code == 200
        assert len(g2.json()["thread"]["messages"]) == 4

        threads_list = client.get(f"/api/grammar/threads/{book1['id']}/{ch1_idx}").json()["threads"]
        assert len(threads_list) == 1

        # 6. LLM offline resilience on uncached word
        monkeypatch.setattr(
            llm,
            "generate_text",
            AsyncMock(side_effect=llm.LLMUnavailableError("Ollama stopped")),
        )
        r_down = client.post(
            "/api/lookup/jj",
            json={
                "lemma": "透き通る",
                "reading": "すきとおる",
                "surface": "透き通った",
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
            },
        )
        assert r_down.status_code == 200
        assert r_down.json()["llm_unavailable"] is True

        # 7. Settings GET/PUT with API key masking
        put_set = client.put(
            "/api/settings",
            json={
                "llm_provider": "gemini",
                "gemini_api_key": "AIzaSyTestSecretKey123456",
                "reading_mode": "vertical",
            },
        )
        assert put_set.status_code == 200
        s_data = put_set.json()
        assert s_data["llm_provider"] == "gemini"
        assert s_data["reading_mode"] == "vertical"
        assert s_data["has_gemini_key"] is True
        assert "AIzaSyTestSecretKey123456" not in json.dumps(s_data)
        assert "3456" in s_data["gemini_api_key_masked"]

        # 8. Progress save
        prog_resp = client.put(
            f"/api/books/{book1['id']}/progress",
            json={"chapter_index": ch1_idx, "scroll_ratio": 0.42, "reading_seconds_delta": 15},
        )
        assert prog_resp.status_code == 200
        assert prog_resp.json()["progress"]["reading_seconds"] == 15


def test_bunsetsu_and_particle_helpers():
    samples = [
        "駅前にはお店が立ち並ぶ商店街がある。",
        "青い空の下で透き通った水が静かに流れている。",
        "歩きながら友達と笑いあった。",
    ]
    for s in samples:
        chunks = chunk_into_bunsetsu(s)
        assert 3 <= len(chunks) <= 6
        assert "".join(chunks) == s.replace(" ", "").replace("\u3000", "")

        pq = extract_particle_quiz(s)
        assert pq is not None
        assert "＿＿" in pq["blanked_sentence"]
        assert len(pq["choices"]) == 4
        assert len(set(pq["choices"])) == 4
        assert pq["correct"] in pq["choices"]
        assert pq["full_sentence"] == s


def test_quiz_and_trouble_lifecycle(temp_env, monkeypatch: pytest.MonkeyPatch):
    async def fake_generate_text(
        *, settings, system_prompt, messages, expect_json=False, json_schema=None
    ):
        if expect_json:
            return (
                json.dumps(
                    {
                        "simple_definition": "すっきりと澄んでいること。",
                        "part_of_speech": "動詞",
                        "context_note": "水がきれいな様子です。",
                        "examples": [{"jp": "湖の水が透き通っている。", "reading": "みずうみ"}],
                    },
                    ensure_ascii=False,
                ),
                "ollama",
                "qwen3:8b",
            )
        return "- **〜ている** indicates an ongoing state.", "ollama", "qwen3:8b"

    monkeypatch.setattr(llm, "generate_text", fake_generate_text)

    with TestClient(app) as client:
        books = client.get("/api/books").json()["books"]
        book1 = books[0]
        ch1_idx = next(c["chapter_index"] for c in book1["chapters"] if c["title"] == "１")

        # Tokenize chapter 1 first
        ch_resp = client.get(f"/api/books/{book1['id']}/chapters/{ch1_idx}").json()
        assert "trouble_keys" in ch_resp
        assert "sticky_keys" in ch_resp
        assert ch_resp["chapter_trouble_count"] == 0

        # Log a light J-J lookup (score = 0.5) and a grammar ask (score = 2.5)
        client.post(
            "/api/lookup/jj",
            json={
                "lemma": "透き通る",
                "reading": "すきとおる",
                "surface": "透き通っ",
                "sentence_text": "青い空の下で透き通った水が静かに流れている。",
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s1",
            },
        )
        client.post(
            "/api/grammar/ask",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s2",
                "target_text": "聞こえる",
                "question": "",
            },
        )

        # Verify chapter metadata reflects trouble items & progressive furigana keys
        ch_after = client.get(f"/api/books/{book1['id']}/chapters/{ch1_idx}").json()
        assert "透き通る|すきとおる" in ch_after["trouble_keys"]
        assert ch_after["chapter_trouble_count"] >= 2

        trouble_items = client.get(
            f"/api/trouble?book_id={book1['id']}"
        ).json()["items"]
        word_trouble = next(i for i in trouble_items if i["item_key"] == "透き通る|すきとおる")
        grammar_trouble = next(i for i in trouble_items if i["item_type"] == "grammar")
        assert word_trouble["sent_text"] == "青い空の下で透き通った水が静かに流れている。"
        assert grammar_trouble["sent_text"]

        # Generate adaptive 5-question quiz
        quiz = client.post(
            "/api/quiz/generate",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "trigger_type": "chapter",
                "max_questions": 5,
            },
        ).json()
        questions = quiz["questions"]
        assert len(questions) == 5
        assert quiz["trouble_based_count"] >= 2

        kinds = [q["kind"] for q in questions]
        assert "scramble" in kinds
        assert "sentence_output" not in kinds
        assert kinds.count("cloze") <= 1
        non_cloze_vocab = [
            q
            for q in questions
            if q["kind"]
            in {"typed_reading", "reading_recall", "meaning_match", "def_to_word", "match_pairs"}
        ]
        assert len(non_cloze_vocab) >= 1

        for q in questions:
            if q["kind"] == "scramble":
                assert q["chunks"] != q["correct_answer"]
                assert sorted(q["chunks"]) == sorted(q["correct_answer"])
            elif q["kind"] == "match_pairs":
                assert len(q["pairs"]) == 4
                assert len(q["left_items"]) == 4
                assert len(q["right_items"]) == 4
                assert {x["id"] for x in q["left_items"]} == {0, 1, 2, 3}
                assert {x["id"] for x in q["right_items"]} == {0, 1, 2, 3}
            elif q["kind"] in {"cloze", "particle"}:
                assert "＿＿" in q["blanked_sentence"]
                assert len(q["choices"]) == 4
                assert q["correct_answer"] in q["choices"]
            else:
                assert q["kind"] in {"typed_reading", "reading_recall", "meaning_match", "def_to_word"}
                if q["kind"] == "typed_reading":
                    assert q["prompt_word"]
                    assert q["correct_answer"]
                else:
                    assert len(q["choices"]) == 4
                    assert q["correct_answer"] in q["choices"]

        # Submit quiz with all answers correct -> advances Stage 0 -> Stage 1 (cool-down, NOT prematurely retired)
        submit_results = [
            {
                "question_id": q["question_id"],
                "kind": q["kind"],
                "trouble_item_id": q["trouble_item_id"],
                "trouble_item_ids": q.get("trouble_item_ids", []),
                "correct": True,
            }
            for q in questions
        ]
        sub_res = client.post(
            "/api/quiz/submit",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "trigger_type": "chapter",
                "results": submit_results,
            },
        ).json()
        assert sub_res["score"] == 5
        assert sub_res["total"] == 5
        word_stage1 = next(c for c in sub_res["conquered"] if c["display_text"] == "透き通る")
        assert word_stage1["memory_stage"] == 1
        assert word_stage1["retired"] is False
        assert word_stage1["next_review_after"] is not None

        # Multi-stage spacing over real time: Stage 1 (+21h) -> Stage 2 (+73h) -> Stage 3 (retired)
        t_base = datetime.now(timezone.utc)
        s2 = db.apply_quiz_item_result(word_stage1["id"], True, now_dt=t_base + timedelta(hours=21))
        assert s2["memory_stage"] == 2
        assert s2["retired"] == 0

        s3 = db.apply_quiz_item_result(word_stage1["id"], True, now_dt=t_base + timedelta(hours=95))
        assert s3["memory_stage"] == 3
        assert s3["retired"] == 1

        # Wrong answer resets back to Stage 0
        reset = db.apply_quiz_item_result(word_stage1["id"], False, now_dt=t_base + timedelta(hours=96))
        assert reset["memory_stage"] == 0
        assert reset["retired"] == 0

        # Manual retire / restore toggle
        retired = client.post(
            f"/api/trouble/{word_stage1['id']}/retire",
            json={"retired": True},
        ).json()["item"]
        assert retired["retired"] == 1
        assert retired["memory_stage"] == 3

        restored = client.post(
            f"/api/trouble/{word_stage1['id']}/retire",
            json={"retired": False},
        ).json()["item"]
        assert restored["retired"] == 0
        assert restored["memory_stage"] == 0

        # Quiz history persisted
        hist = client.get(f"/api/quiz/history?book_id={book1['id']}").json()["sessions"]
        assert len(hist) == 1
        assert hist[0]["correct_count"] == 5
        assert hist[0]["total_questions"] == 5


def test_notes_and_session_quiz(temp_env):
    with TestClient(app) as client:
        books = client.get("/api/books").json()["books"]
        book1 = books[0]
        ch1_idx = next(c["chapter_index"] for c in book1["chapters"] if c["title"] == "１")
        ch2_idx = next(c["chapter_index"] for c in book1["chapters"] if c["title"] == "２")

        # Empty quote + empty note rejected
        bad = client.post(
            "/api/notes",
            json={"book_id": book1["id"], "chapter_index": ch1_idx, "quote_text": "  ", "note_text": ""},
        )
        assert bad.status_code == 400

        # 1) Highlighted quote + note tied to sentence
        n1 = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s1",
                "quote_text": "駅前にはお店が立ち並ぶ商店街がある。",
                "note_text": "Great imagery of the town street.",
                "category": "quote",
            },
        )
        assert n1.status_code == 200
        note1 = n1.json()["note"]
        assert note1["book_title"] == book1["title"]

        # 2) Quote-only save
        n2 = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "quote_text": "田舎の朝には、いつも小鳥の声が聞こえる。",
                "category": "expression",
            },
        )
        assert n2.status_code == 200

        # 3) Free-form note with NO quote and NO lookups
        n3 = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch2_idx,
                "note_text": "Remember how afternoon wind sets the mood for chapter 2.",
                "category": "note",
            },
        )
        assert n3.status_code == 200
        note3 = n3.json()["note"]

        # Chapter metadata includes saved sentence IDs
        ch_meta = client.get(f"/api/books/{book1['id']}/chapters/{ch1_idx}").json()
        assert f"c{ch1_idx}_s1" in ch_meta["note_sent_ids"]

        # Filtering by chapter / category
        ch1_notes = client.get(f"/api/notes?book_id={book1['id']}&chapter_index={ch1_idx}").json()["notes"]
        assert len(ch1_notes) == 2
        only_notes = client.get(f"/api/notes?book_id={book1['id']}&category=note").json()["notes"]
        assert len(only_notes) == 1
        assert only_notes[0]["id"] == note3["id"]

        # Update & delete
        upd = client.put(
            f"/api/notes/{note3['id']}",
            json={"note_text": "Updated chapter 2 reflection.", "category": "note"},
        )
        assert upd.status_code == 200
        assert upd.json()["note"]["note_text"] == "Updated chapter 2 reflection."

        del_r = client.delete(f"/api/notes/{n2.json()['note']['id']}")
        assert del_r.status_code == 200
        assert len(client.get(f"/api/notes?book_id={book1['id']}").json()["notes"]) == 2

        # Session quiz prioritization: create an older high-score trouble word, then a fresh lower-score word
        client.get(f"/api/books/{book1['id']}/chapters/{ch2_idx}")
        with db.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO trouble_items
                    (item_type, item_key, display_text, reading, book_id, last_chapter_index,
                     sent_id, sent_text, en_reveals, trouble_score, retired, updated_at)
                VALUES ('word', '立ち並ぶ|たちならぶ', '立ち並ぶ', 'たちならぶ', ?, ?, ?,
                        '駅前にはお店が立ち並ぶ商店街がある。', 2, 9.0, 0, '2026-01-01T00:00:00+00:00')
                """,
                (book1["id"], ch1_idx, f"c{ch1_idx}_s1"),
            )

        session_start = "2026-11-22T00:00:00Z"
        # Look up a Chapter 2 word AFTER session_start
        client.post(
            "/api/lookup/en",
            json={
                "lemma": "冷たい",
                "norm_form": "冷たい",
                "surface": "冷たく",
                "reading": "つめたい",
                "book_id": book1["id"],
                "chapter_index": ch2_idx,
                "sent_id": f"c{ch2_idx}_s1",
                "sentence_text": "午後になって、風が少し冷たくなった。",
            },
        )

        sess_quiz = client.post(
            "/api/quiz/generate",
            json={
                "book_id": book1["id"],
                "chapter_index": ch2_idx,
                "trigger_type": "session",
                "max_questions": 5,
                "session_started_at": session_start,
            },
        ).json()
        qs = sess_quiz["questions"]
        assert qs[0]["target_display"] == "冷たい"


def test_non_cloze_vocab_question_builders(temp_env):
    vocab_pool = [
        {"lemma": "風景", "surface": "風景", "reading": "ふうけい", "pos": "名詞", "sent_id": "s0", "sentence_text": "きれいな風景。"},
        {"lemma": "記憶", "surface": "記憶", "reading": "きおく", "pos": "名詞", "sent_id": "s1", "sentence_text": "古い記憶。"},
        {"lemma": "勇気", "surface": "勇気", "reading": "ゆうき", "pos": "名詞", "sent_id": "s2", "sentence_text": "勇気を出す。"},
        {"lemma": "秘密", "surface": "秘密", "reading": "ひみつ", "pos": "名詞", "sent_id": "s3", "sentence_text": "小さな秘密。"},
    ]

    # Poison dict_cache with bogus LLM output — quizzes MUST ignore LLM jj_json/examples
    with db.get_conn() as conn:
        for w, r in (
            ("風景", "ふうけい"),
            ("記憶", "きおく"),
            ("勇気", "ゆうき"),
            ("秘密", "ひみつ"),
            ("商店街", "しょうてんがい"),
        ):
            conn.execute(
                """
                INSERT INTO dict_cache (lemma, reading, jj_json, provider, model, updated_at)
                VALUES (?, ?, ?, 'ollama', 'm', '2026-11-22T00:00:00+00:00')
                ON CONFLICT(lemma, reading) DO UPDATE SET jj_json = excluded.jj_json
                """,
                (
                    w,
                    r,
                    json.dumps(
                        {
                            "simple_definition": f"POISONED LLM DEF {w}",
                            "examples": [{"jp": f"FAKE LLM SENTENCE {w}", "reading": ""}],
                        },
                        ensure_ascii=False,
                    ),
                ),
            )

    # 1. Reading distractors & Reading Recall (strictly JMdict-verified, no valid readings leaked)
    dists = quiz_service._generate_reading_distractors("しょうてんがい", vocab_pool)
    assert len(dists) == 3
    assert len(set(dists)) == 3
    assert "しょうてんがい" not in dists

    rq = quiz_service._build_reading_recall_question(
        q_idx=0,
        lemma="商店街",
        reading="しょうてんがい",
        vocab_pool=vocab_pool,
        trouble_item_id=None,
    )
    assert rq is not None
    assert rq["kind"] == "reading_recall"
    assert rq["prompt_word"] == "商店街"
    assert "<rt>" not in rq["prompt_word"]
    assert len(set(rq["choices"])) == 4
    assert rq["correct_answer"] == "しょうてんがい"
    assert "POISONED LLM DEF" not in rq["hint"]
    assert "POISONED LLM DEF" not in rq["explanation"]

    # 2. Meaning Match (strictly offline JMdict dictionary meaning, ignores LLM jj_json)
    mq_en = quiz_service._build_meaning_match_question(
        q_idx=1,
        lemma="風景",
        reading="ふうけい",
        vocab_pool=vocab_pool,
        trouble_item_id=None,
    )
    assert mq_en is not None
    assert mq_en["kind"] == "meaning_match"
    assert len(set(mq_en["choices"])) == 4
    assert mq_en["correct_answer"] in mq_en["choices"]
    assert "scenery" in mq_en["correct_answer"].lower()
    assert all("POISONED LLM DEF" not in c for c in mq_en["choices"])

    # 3. Reverse Dictionary Definition -> Word (strictly JMdict)
    dq = quiz_service._build_def_to_word_question(
        q_idx=2,
        lemma="記憶",
        reading="きおく",
        vocab_pool=vocab_pool,
        trouble_item_id=None,
    )
    assert dq is not None
    assert dq["kind"] == "def_to_word"
    assert "memory" in dq["prompt_text"].lower()
    assert "POISONED LLM DEF" not in dq["prompt_text"]
    assert len(set(dq["choices"])) == 4
    assert dq["correct_answer"] == "記憶"

    # 4. Cloze strictly uses real book text (never LLM jj_examples)
    cq = quiz_service._build_cloze_question(
        q_idx=3,
        lemma="風景",
        reading="ふうけい",
        surface_candidates=["風景"],
        preferred_sentence="窓から綺麗な風景が見える。",
        sentences=[{"sent_id": "s0", "text": "窓から綺麗な風景が見える。"}],
        vocab_pool=vocab_pool,
        trouble_item_id=None,
    )
    assert cq is not None
    assert cq["full_sentence"] == "窓から綺麗な風景が見える。"
    assert "FAKE LLM SENTENCE" not in cq["full_sentence"]
    assert cq["blanked_sentence"].replace("＿＿＿", cq["correct_answer"], 1) == cq["full_sentence"]

    # 5. 4-Pair Match-Up (JMdict meanings only) + multi-item trouble credit on submit
    with db.get_conn() as conn:
        t1 = int(
            conn.execute(
                """
                INSERT INTO trouble_items
                    (item_type, item_key, display_text, reading, trouble_score, retired, updated_at)
                VALUES ('word', '風景|ふうけい', '風景', 'ふうけい', 1.5, 0, '2026-11-22T00:00:00+00:00')
                """
            ).lastrowid
        )
        t2 = int(
            conn.execute(
                """
                INSERT INTO trouble_items
                    (item_type, item_key, display_text, reading, trouble_score, retired, updated_at)
                VALUES ('word', '記憶|きおく', '記憶', 'きおく', 1.5, 0, '2026-11-22T00:00:00+00:00')
                """
            ).lastrowid
        )

    mp = quiz_service._build_match_pairs_question(
        q_idx=5,
        candidate_words=[
            {"lemma": "風景", "reading": "ふうけい", "trouble_item_id": t1},
            {"lemma": "記憶", "reading": "きおく", "trouble_item_id": t2},
            {"lemma": "勇気", "reading": "ゆうき", "trouble_item_id": None},
            {"lemma": "秘密", "reading": "ひみつ", "trouble_item_id": None},
        ],
    )
    assert mp is not None
    assert mp["kind"] == "match_pairs"
    assert len(mp["pairs"]) == 4
    assert all("POISONED LLM DEF" not in p["meaning"] for p in mp["pairs"])
    assert set(mp["trouble_item_ids"]) == {t1, t2}

    sub = quiz_service.submit_quiz(
        book_id=None,
        chapter_index=None,
        trigger_type="manual",
        results=[
            {
                "question_id": "q_5",
                "kind": "match_pairs",
                "trouble_item_id": None,
                "trouble_item_ids": mp["trouble_item_ids"],
                "correct": True,
            }
        ],
    )
    assert sub["score"] == 1
    assert len(sub["conquered"]) == 2
    assert all(c["memory_stage"] == 1 and not c["retired"] for c in sub["conquered"])


def test_llamacpp_provider_and_settings(temp_env, monkeypatch: pytest.MonkeyPatch):
    captured_requests = []

    class FakeResponse:
        def __init__(self, status_code: int, body: dict):
            self.status_code = status_code
            self._body = body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("err", request=None, response=None)

        def json(self):
            return self._body

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            captured_requests.append(("GET", url, headers))
            return FakeResponse(200, {"data": [{"id": "Qwen3-8B-Q4_K_M.gguf"}]})

        async def post(self, url, json=None, headers=None):
            captured_requests.append(("POST", url, json, headers))
            return FakeResponse(
                200,
                {
                    "model": "Qwen3-8B-Q4_K_M.gguf",
                    "choices": [
                        {
                            "message": {
                                "content": '<think>reasoning</think>\n{"simple_definition": "静かで落ち着いていること。", "part_of_speech": "形容動詞", "context_note": "朝の雰囲気を表します。", "examples": [{"jp": "森は静かだ。", "reading": "もり・しずか"}]}'
                            }
                        }
                    ],
                },
            )

    monkeypatch.setattr(llm.httpx, "AsyncClient", FakeAsyncClient)

    with TestClient(app) as client:
        # Update settings to llama.cpp with bearer token and verify secret masking
        s_put = client.put(
            "/api/settings",
            json={
                "llm_provider": "llamacpp",
                "llamacpp_url": "http://127.0.0.1:8080",
                "llamacpp_api_key": "local-secret-token-9999",
            },
        )
        assert s_put.status_code == 200
        s_json = s_put.json()
        assert s_json["llm_provider"] == "llamacpp"
        assert s_json["has_llamacpp_key"] is True
        assert "local-secret-token-9999" not in json.dumps(s_json)
        assert "9999" in s_json["llamacpp_api_key_masked"]

        models_res = client.get("/api/llm/llamacpp-models").json()
        assert models_res["reachable"] is True
        assert models_res["models"] == ["Qwen3-8B-Q4_K_M.gguf"]

        jj = client.post(
            "/api/lookup/jj",
            json={
                "lemma": "静か",
                "reading": "しずか",
                "surface": "静か",
                "sentence_text": "田舎の静かな朝。",
            },
        ).json()
        assert jj["llm_unavailable"] is False
        assert jj["provider"] == "llamacpp"
        assert jj["model"] == "Qwen3-8B-Q4_K_M.gguf"
        assert jj["jj"]["simple_definition"] == "静かで落ち着いていること。"

        post_call = next(r for r in captured_requests if r[0] == "POST")
        assert post_call[1] == "http://127.0.0.1:8080/v1/chat/completions"
        assert post_call[2]["response_format"]["type"] == "json_schema"
        assert "simple_definition" in post_call[2]["response_format"]["json_schema"]["schema"]["required"]
        assert post_call[3]["Authorization"] == "Bearer local-secret-token-9999"


def test_long_term_quiz_mixing_and_ten_question_default(temp_env):
    with TestClient(app) as client:
        books = client.get("/api/books").json()["books"]
        book1 = books[0]
        ch1_idx = next(c["chapter_index"] for c in book1["chapters"] if c["title"] == "１")
        client.get(f"/api/books/{book1['id']}/chapters/{ch1_idx}")

        past_due = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        future_cool = (datetime.now(timezone.utc) + timedelta(hours=18)).isoformat()

        with db.get_conn() as conn:
            # 1) Ready aged item (Stage 1, past due) -> MUST appear in quiz with is_aged=True
            conn.execute(
                """
                INSERT INTO trouble_items
                    (item_type, item_key, display_text, reading, book_id, last_chapter_index,
                     sent_text, trouble_score, memory_stage, next_review_after, retired, updated_at)
                VALUES ('word', '商店街|しょうてんがい', '商店街', 'しょうてんがい', ?, ?,
                        '駅前にはお店が立ち並ぶ商店街がある。', 2.0, 1, ?, 0, '2026-11-20T00:00:00+00:00')
                """,
                (book1["id"], ch1_idx, past_due),
            )
            # 2) Cooling-down item (Stage 1, due in future) -> MUST NOT appear in prioritized queue
            conn.execute(
                """
                INSERT INTO trouble_items
                    (item_type, item_key, display_text, reading, book_id, last_chapter_index,
                     sent_text, trouble_score, memory_stage, next_review_after, retired, updated_at)
                VALUES ('word', '小鳥|ことり', '小鳥', 'ことり', ?, ?,
                        '田舎の朝には、いつも小鳥の声が聞こえる。', 5.0, 1, ?, 0, '2026-11-22T00:00:00+00:00')
                """,
                (book1["id"], ch1_idx, future_cool),
            )

        quiz = client.post(
            "/api/quiz/generate",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "trigger_type": "chapter",
                "max_questions": 5,
            },
        ).json()
        qs = quiz["questions"]
        assert len(qs) == 5
        assert quiz["aged_count"] >= 1
        assert qs[0]["target_display"] == "商店街"
        assert qs[0]["is_aged"] is True
        assert qs[0]["kind"] == "typed_reading"
        assert all(q["target_display"] != "小鳥" for q in qs)
        assert all(q["kind"] != "sentence_output" for q in qs)

        # Default max_questions is 10 and never generates sentence_output
        quiz_default = client.post(
            "/api/quiz/generate",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "trigger_type": "chapter",
            },
        ).json()
        qs_default = quiz_default["questions"]
        assert len(qs_default) == 10
        assert all(q["kind"] != "sentence_output" for q in qs_default)
        assert [q["question_id"] for q in qs_default] == [f"q_{i}" for i in range(10)]

        # Output evaluation endpoint removed
        assert (
            client.post(
                "/api/quiz/evaluate-output",
                json={"target_word": "商店街", "user_sentence": "商店街に行く。"},
            ).status_code
            == 404
        )


def test_llamacpp_manager_and_autostart_settings(
    temp_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # find_llama_server_binary prefers %LOCALAPPDATA%\Shiori\bin\llama-cuda\llama-server.exe
    cuda_exe = tmp_path / "Shiori" / "bin" / "llama-cuda" / "llama-server.exe"
    cuda_exe.parent.mkdir(parents=True)
    cuda_exe.write_bytes(b"MZ")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(llamacpp_manager.sys, "platform", "win32")
    assert llamacpp_manager.find_llama_server_binary() == cuda_exe

    # parse_local_host_port allows loopback and rejects remote hosts
    assert llamacpp_manager.parse_local_host_port("http://localhost:8080") == ("127.0.0.1", 8080)
    assert llamacpp_manager.parse_local_host_port("http://127.0.0.1") == ("127.0.0.1", 8080)
    assert llamacpp_manager.parse_local_host_port("http://192.168.1.50:8080") is None
    assert llamacpp_manager.parse_local_host_port("ftp://127.0.0.1:8080") is None

    # resolve_model_path supports explicit path & auto-discovery skipping .part/.tmp files
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "downloading.part.gguf").write_bytes(b"GGUF")
    (models_dir / "temp.tmp.gguf").write_bytes(b"GGUF")
    (models_dir / "incomplete.gguf.part").write_bytes(b"GGUF")
    real_model = models_dir / "qwen-local.gguf"
    real_model.write_bytes(b"GGUF")

    explicit_model = tmp_path / "explicit.gguf"
    explicit_model.write_bytes(b"GGUF")

    assert llamacpp_manager.resolve_model_path("", extra_dirs=[models_dir]) == real_model
    assert (
        llamacpp_manager.resolve_model_path(str(explicit_model), extra_dirs=[models_dir])
        == explicit_model
    )

    # Guard prevents spawning inside pytest unless allow_in_tests=True
    assert (
        llamacpp_manager.ensure_managed_server_running(
            {
                "llm_provider": "llamacpp",
                "llamacpp_autostart": "true",
                "llamacpp_model_path": str(real_model),
            }
        )
        is False
    )

    # ManagedLlamaServer skips spawning when disabled, non-llamacpp, or remote URL
    for cfg in (
        {"llm_provider": "ollama", "llamacpp_autostart": "true", "llamacpp_url": "http://127.0.0.1:8080"},
        {"llm_provider": "llamacpp", "llamacpp_autostart": "false", "llamacpp_url": "http://127.0.0.1:8080"},
        {"llm_provider": "llamacpp", "llamacpp_autostart": "true", "llamacpp_url": "http://10.0.0.5:8080"},
    ):
        mgr = llamacpp_manager.ManagedLlamaServer(cfg, log_path=tmp_path / "srv.log")
        assert mgr.start_if_needed(wait_timeout=0.5) is False
        assert mgr.proc is None

    # Settings API persists llamacpp_model_path & llamacpp_autostart and hooks lifecycle
    ensure_calls: list[dict[str, str]] = []
    stop_calls: list[bool] = []
    monkeypatch.setattr(
        llamacpp_manager,
        "ensure_managed_server_running",
        lambda s, *args, **kwargs: ensure_calls.append(dict(s)) or True,
    )
    monkeypatch.setattr(
        llamacpp_manager,
        "stop_managed_server",
        lambda: stop_calls.append(True),
    )

    with TestClient(app) as client:
        res = client.put(
            "/api/settings",
            json={
                "llm_provider": "llamacpp",
                "llamacpp_model_path": str(real_model),
                "llamacpp_autostart": "false",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["llamacpp_model_path"] == str(real_model)
        assert body["llamacpp_autostart"] == "false"
        assert len(ensure_calls) == 0
        assert len(stop_calls) == 1

        # Enabling autostart on llamacpp triggers ensure_managed_server_running
        res_on = client.put(
            "/api/settings",
            json={"llm_provider": "llamacpp", "llamacpp_autostart": "true"},
        )
        assert res_on.status_code == 200
        assert len(ensure_calls) == 1

        # Switching to ollama triggers stop_managed_server
        res_off = client.put("/api/settings", json={"llm_provider": "ollama"})
        assert res_off.status_code == 200
        assert len(stop_calls) == 2

        fetched = client.get("/api/settings").json()
        assert fetched["llamacpp_model_path"] == str(real_model)
        assert fetched["llamacpp_autostart"] == "true"

    # Calling llm.generate_text for llamacpp invokes ensure_managed_server_running
    ensure_calls.clear()
    monkeypatch.setattr(llm, "_llamacpp_chat", AsyncMock(return_value=("了解", "qwen2.5-7b")))
    text, provider, model_used = asyncio.run(
        llm.generate_text(
            settings={
                "llm_provider": "llamacpp",
                "llamacpp_url": "http://127.0.0.1:8080",
                "llamacpp_model": "qwen2.5-7b",
            },
            system_prompt="sys",
            messages=[{"role": "user", "content": "hi"}],
        )
    )
    assert (text, provider, model_used) == ("了解", "llamacpp", "qwen2.5-7b")
    assert len(ensure_calls) == 1


def test_jj_json_resilience_and_english_grammar_prompt(temp_env, monkeypatch: pytest.MonkeyPatch):
    # 1) _llamacpp_chat falls back cleanly from json_schema (400) -> json_object (200)
    sent_rf_types: list[str | None] = []

    class FallbackResp:
        def __init__(self, status_code: int, body: dict):
            self.status_code = status_code
            self._body = body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("err", request=None, response=None)

        def json(self):
            return self._body

    class FallbackClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            rf = (json or {}).get("response_format")
            rf_type = rf.get("type") if isinstance(rf, dict) else None
            sent_rf_types.append(rf_type)
            if rf_type == "json_schema":
                return FallbackResp(400, {"error": "json_schema unsupported"})
            return FallbackResp(
                200,
                {
                    "model": "qwen2.5-7b",
                    "choices": [{"message": {"content": '{"simple_definition": "静かな様子。"}'}}],
                },
            )

    monkeypatch.setattr(llm.httpx, "AsyncClient", FallbackClient)
    fb_text, fb_model = asyncio.run(
        llm._llamacpp_chat(
            "http://127.0.0.1:8080",
            "qwen2.5-7b",
            "sys",
            [{"role": "user", "content": "hi"}],
            expect_json=True,
            json_schema=lookup_service._JJ_JSON_SCHEMA,
        )
    )
    assert sent_rf_types == ["json_schema", "json_object"]
    assert fb_model == "qwen2.5-7b"
    assert "静かな様子。" in fb_text

    # 2) _normalize_jj_payload handles Japanese/English key aliases and nested wrappers
    norm_jp = lookup_service._normalize_jj_payload(
        {
            "意味": "たくさんの店が並ぶ通り。",
            "品詞": "名詞",
            "ニュアンス": "町の様子。",
            "例文": [{"例文": "駅前の商店街を歩く。", "読み": "しょうてんがい"}],
        }
    )
    assert norm_jp["simple_definition"] == "たくさんの店が並ぶ通り。"
    assert norm_jp["part_of_speech"] == "名詞"
    assert norm_jp["context_note"] == "町の様子。"
    assert norm_jp["examples"] == [{"jp": "駅前の商店街を歩く。", "reading": "しょうてんがい"}]

    norm_wrapped = lookup_service._normalize_jj_payload(
        {"result": {"definition": "水がきれいで透きとおっていること。", "pos": "動詞"}}
    )
    assert norm_wrapped["simple_definition"] == "水がきれいで透きとおっていること。"
    assert norm_wrapped["part_of_speech"] == "動詞"

    # 3) lookup_jj retries once when first response omits simple_definition and succeeds on retry
    jj_attempts: list[str] = []

    async def fake_retry_generate(
        *, settings, system_prompt, messages, expect_json=False, json_schema=None
    ):
        jj_attempts.append(messages[-1]["content"])
        if len(jj_attempts) == 1:
            return '{"part_of_speech": "名詞"}', "llamacpp", "qwen2.5-7b"
        return (
            json.dumps(
                {
                    "simple_definition": "町にある店の並び。",
                    "part_of_speech": "名詞",
                    "context_note": "日常の買い物をする場所。",
                    "examples": [],
                },
                ensure_ascii=False,
            ),
            "llamacpp",
            "qwen2.5-7b",
        )

    monkeypatch.setattr(llm, "generate_text", fake_retry_generate)
    jj_res = asyncio.run(
        lookup_service.lookup_jj(
            lemma="商店街",
            reading="しょうてんがい",
            surface="商店街",
            sentence_text="駅前に商店街がある。",
        )
    )
    assert len(jj_attempts) == 2
    assert "simple_definition" in jj_attempts[1]
    assert jj_res["llm_unavailable"] is False
    assert jj_res["jj"]["simple_definition"] == "町にある店の並び。"

    # 4) ask_grammar sends English system & default user prompts and stores English display_user_msg
    captured_grammar_calls: list[tuple[str, str]] = []

    async def fake_grammar_generate(
        *, settings, system_prompt, messages, expect_json=False, json_schema=None
    ):
        captured_grammar_calls.append((system_prompt, messages[-1]["content"]))
        return (
            "- **立ち並ぶ (たちならぶ)**: stand in a row.\n- Particle **には** marks the station area.",
            "llamacpp",
            "qwen2.5-7b",
        )

    monkeypatch.setattr(llm, "generate_text", fake_grammar_generate)
    with TestClient(app) as client:
        books = client.get("/api/books").json()["books"]
        book1 = books[0]
        ch1_idx = next(c["chapter_index"] for c in book1["chapters"] if c["title"] == "１")
        client.get(f"/api/books/{book1['id']}/chapters/{ch1_idx}")

        g_res = client.post(
            "/api/grammar/ask",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s0",
                "target_text": "",
                "question": "",
            },
        )
        assert g_res.status_code == 200
        thread = g_res.json()["thread"]
        assert thread["messages"][0]["content"] == "Break down the grammar and nuance of this sentence."

        g_phrase = client.post(
            "/api/grammar/ask",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s0",
                "target_text": "立ち並ぶ",
                "question": "",
            },
        )
        assert g_phrase.status_code == 200
        assert (
            g_phrase.json()["thread"]["messages"][0]["content"]
            == 'Explain the grammar and nuance of "立ち並ぶ" in this sentence.'
        )

    assert len(captured_grammar_calls) == 2
    sys_p, user_p = captured_grammar_calls[0]
    assert "strictly in clear, concise English" in sys_p
    assert "[Target Sentence]" in user_p
    assert "[Question]" in user_p
    assert "concise English" in user_p


def test_selection_lookup_custom_chat_and_exact_quiz_invariants(
    temp_env, monkeypatch: pytest.MonkeyPatch
):
    # 1) JMdict exact matching rejects mismatched readings/compounds and tracks all valid readings
    bogus = dictionary.lookup_english_exact("魔導帽子", "あかり")
    assert bogus["entries"] == []
    assert bogus["all_valid_readings"] == []

    ashita = dictionary.lookup_english_exact("明日")
    assert "あした" in ashita["all_valid_readings"]
    assert "あす" in ashita["all_valid_readings"]

    # 2) typed_reading includes accepted_answers & distractors never include another valid reading
    tq = quiz_service._build_typed_reading_question(
        q_idx=0,
        lemma="明日",
        reading="あした",
        vocab_pool=[],
        trouble_item_id=None,
    )
    assert tq is not None
    assert "あした" in tq["accepted_answers"]
    assert "あす" in tq["accepted_answers"]
    for alt in ashita["all_valid_readings"]:
        if alt != "あした":
            assert alt not in tq["choices"]

    dists_ashita = quiz_service._generate_reading_distractors(
        "あした", [], forbidden_readings=set(ashita["all_valid_readings"])
    )
    assert all(r not in dists_ashita for r in ashita["all_valid_readings"])

    # Unverified reading/kanji returns None rather than guessing
    assert (
        quiz_service._build_reading_recall_question(
            q_idx=0, lemma="魔導帽子", reading="まどうぼうし", vocab_pool=[], trouble_item_id=None
        )
        is None
    )

    # 3) Cloze never blanks a mid-word substring (e.g. 手 inside 相手) and restores exact text
    assert (
        quiz_service._build_cloze_question(
            q_idx=0,
            lemma="手",
            reading="て",
            surface_candidates=["手"],
            preferred_sentence="相手の話をよく聞く。",
            sentences=[{"sent_id": "s0", "text": "相手の話をよく聞く。"}],
            vocab_pool=[],
            trouble_item_id=None,
        )
        is None
    )
    real_cloze = quiz_service._build_cloze_question(
        q_idx=0,
        lemma="商店街",
        reading="しょうてんがい",
        surface_candidates=["商店街"],
        preferred_sentence="駅前にはお店が立ち並ぶ商店街がある。",
        sentences=[{"sent_id": "s0", "text": "駅前にはお店が立ち並ぶ商店街がある。"}],
        vocab_pool=[],
        trouble_item_id=None,
    )
    assert real_cloze is not None
    assert (
        real_cloze["blanked_sentence"].replace("＿＿＿", real_cloze["correct_answer"], 1)
        == real_cloze["full_sentence"]
    )

    # 4) Particle Fix skips compound-particle halves (には) and restores exact text
    pq = extract_particle_quiz("駅前にはお店がある。")
    assert pq is not None
    assert pq["correct"] == "が"
    assert pq["blanked_sentence"].replace("＿＿", pq["correct"], 1) == pq["full_sentence"]

    # 5) Scramble rejects lossy whitespace splits and reconstructs exact trimmed sentence
    assert (
        quiz_service._build_scramble_question(
            q_idx=0,
            sentence="ここ は 駅 です。",
            target_display="",
            trouble_item_id=None,
        )
        is None
    )
    scr = quiz_service._build_scramble_question(
        q_idx=0,
        sentence="駅前にはお店が立ち並ぶ商店街がある。",
        target_display="",
        trouble_item_id=None,
    )
    assert scr is not None
    assert "".join(scr["correct_answer"]) == scr["full_sentence"] == "駅前にはお店が立ち並ぶ商店街がある。"

    # 6) Selection Lookup (blank reading) & Custom Chat (explicit question without auto-prompt)
    captured_prompts: list[str] = []

    async def fake_generate(
        *, settings, system_prompt, messages, expect_json=False, json_schema=None
    ):
        captured_prompts.append(messages[-1]["content"])
        if expect_json:
            return (
                json.dumps(
                    {
                        "simple_definition": "美しい景色。",
                        "part_of_speech": "名詞",
                        "context_note": "",
                        "examples": [],
                    },
                    ensure_ascii=False,
                ),
                "llamacpp",
                "qwen2.5-7b",
            )
        return "Custom answer about には.", "llamacpp", "qwen2.5-7b"

    monkeypatch.setattr(llm, "generate_text", fake_generate)

    with TestClient(app) as client:
        sel_jj = client.post(
            "/api/lookup/jj",
            json={
                "lemma": "風景",
                "reading": "",
                "surface": "風景",
                "sentence_text": "窓から美しい風景が見えた。",
            },
        ).json()
        assert sel_jj["llm_unavailable"] is False
        assert sel_jj["reading"] == "ふうけい"

        # Inflected selection EN lookup falls back cleanly to lemma
        sel_en = client.post(
            "/api/lookup/en",
            json={"lemma": "見上げた", "surface": "見上げた", "reading": ""},
        ).json()
        assert sel_en["entries"]
        assert any(
            "look up" in g.lower()
            for e in sel_en["entries"]
            for s in e["senses"]
            for g in s["glosses"]
        )

        books = client.get("/api/books").json()["books"]
        book1 = books[0]
        ch1_idx = next(c["chapter_index"] for c in book1["chapters"] if c["title"] == "１")
        client.get(f"/api/books/{book1['id']}/chapters/{ch1_idx}")

        chat_res = client.post(
            "/api/grammar/ask",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": "selection",
                "target_text": "には",
                "sentence_text": "駅前にはお店がある。",
                "question": "Why is には used?",
            },
        )
        assert chat_res.status_code == 200
        chat_thread = chat_res.json()["thread"]
        assert chat_thread["messages"][0]["content"] == "Why is には used?"
        assert "In concise English only" not in captured_prompts[-1]
        assert "Why is には used?" in captured_prompts[-1]


def _make_solid_png_bytes(width: int, height: int, color: tuple[int, int, int] = (240, 240, 240)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _create_synthetic_manga_epub(epub_path: Path, title: str = "サンプル漫画 第一巻") -> Path:
    """Create a synthetic RTL manga EPUB with SVG cover, regular page, and wide 2-page spread."""
    epub_path.parent.mkdir(parents=True, exist_ok=True)
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
    content_opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>テスト作家</dc:creator>
    <dc:language>ja</dc:language>
  </metadata>
  <manifest>
    <item id="p0" href="text/p0.xhtml" media-type="application/xhtml+xml"/>
    <item id="p1" href="text/p1.xhtml" media-type="application/xhtml+xml"/>
    <item id="p2" href="text/p2.xhtml" media-type="application/xhtml+xml"/>
    <item id="img0" href="images/cover.png" media-type="image/png"/>
    <item id="img1" href="images/p1.png" media-type="image/png"/>
    <item id="img2" href="images/spread.png" media-type="image/png"/>
  </manifest>
  <spine page-progression-direction="rtl">
    <itemref idref="p0"/>
    <itemref idref="p1"/>
    <itemref idref="p2"/>
  </spine>
</package>"""
    p0_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Cover</title></head>
<body><div><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 800 1200">
  <image width="800" height="1200" xlink:href="../images/cover.png"/>
</svg></div></body></html>"""
    p1_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><meta name="viewport" content="width=800, height=1200"/></head>
<body><div class="calibre2"><img src="../images/p1.png" alt=""/></div></body></html>"""
    p2_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><meta name="viewport" content="width=1600, height=1200"/></head>
<body><div class="calibre2"><img src="../images/spread.png" alt=""/></div></body></html>"""

    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/text/p0.xhtml", p0_xhtml)
        zf.writestr("OEBPS/text/p1.xhtml", p1_xhtml)
        zf.writestr("OEBPS/text/p2.xhtml", p2_xhtml)
        zf.writestr("OEBPS/images/cover.png", _make_solid_png_bytes(800, 1200, (220, 230, 245)))
        zf.writestr("OEBPS/images/p1.png", _make_solid_png_bytes(800, 1200, (245, 240, 235)))
        zf.writestr("OEBPS/images/spread.png", _make_solid_png_bytes(1600, 1200, (235, 245, 235)))
    return epub_path


def _create_synthetic_manga_cbz(cbz_path: Path) -> Path:
    """Create a synthetic CBZ archive with out-of-order filenames to test natural sorting."""
    cbz_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(cbz_path, "w") as zf:
        zf.writestr("p10.png", _make_solid_png_bytes(700, 1000, (210, 210, 210)))
        zf.writestr("p2.png", _make_solid_png_bytes(700, 1000, (225, 225, 225)))
        zf.writestr("p1.png", _make_solid_png_bytes(700, 1000, (240, 240, 240)))
    return cbz_path


def test_manga_parser_routes_ocr_and_cross_scan_isolation(tmp_path: Path, monkeypatch):
    """Verify EPUB/CBZ manga parsing, natural sorting, cross-scan safety, OCR caching, and context integration."""
    books_dir = tmp_path / "books"
    manga_dir = tmp_path / "manga"
    nested_dir = manga_dir / "author_sub" / "vol_sub"
    books_dir.mkdir(parents=True)
    nested_dir.mkdir(parents=True)

    _build_sample_epub(books_dir / "novel.epub", title="テスト小説")
    manga_epub = _create_synthetic_manga_epub(nested_dir / "sample_manga.epub")
    manga_cbz = _create_synthetic_manga_cbz(manga_dir / "sample_cbz.cbz")

    # Direct parser checks
    parsed_epub = parse_manga(manga_epub)
    assert parsed_epub.title == "サンプル漫画 第一巻"
    assert parsed_epub.page_progression == "rtl"
    assert [(p.width, p.height, p.is_spread) for p in parsed_epub.pages] == [
        (800, 1200, False),
        (800, 1200, False),
        (1600, 1200, True),
    ]

    parsed_cbz = parse_manga(manga_cbz)
    assert [p.image_href for p in parsed_cbz.pages] == ["p1.png", "p2.png", "p10.png"]

    # Tokenize plain Japanese sentence preserving exact concatenation
    plain = "駅前には商店街がある！"
    toks = tokenize_plain_text(plain)
    assert "".join(t["surface"] for t in toks) == plain
    assert any(t["lemma"] == "商店街" and t["clickable"] for t in toks)

    test_db = tmp_path / "test_manga.db"
    monkeypatch.setattr(config, "BOOKS_DIR", books_dir)
    monkeypatch.setattr(config, "MANGA_DIR", manga_dir)
    monkeypatch.setattr(config, "DB_PATH", test_db)
    db.set_db_path(test_db)
    db.init_db()

    ocr_calls = 0

    def fake_page_ocr(image_bytes: bytes, page_index: int) -> dict:
        nonlocal ocr_calls
        ocr_calls += 1
        return {
            "ocr_available": True,
            "img_width": 800,
            "img_height": 1200,
            "engine": "mock-mokuro",
            "blocks": [
                {
                    "block_id": f"p{page_index}_b0",
                    "box": [100, 80, 320, 420],
                    "box_pct": [12.5, 6.667, 27.5, 28.333],
                    "vertical": True,
                    "lines": ["今日は", "いい天気だね！"],
                    "text": "今日はいい天気だね！",
                    "tokens": tokenize_plain_text("今日はいい天気だね！"),
                },
                {
                    "block_id": f"p{page_index}_b1",
                    "box": [450, 200, 680, 500],
                    "box_pct": [56.25, 16.667, 28.75, 25.0],
                    "vertical": True,
                    "lines": ["駅前の商店街へ行こう。"],
                    "text": "駅前の商店街へ行こう。",
                    "tokens": tokenize_plain_text("駅前の商店街へ行こう。"),
                },
            ],
        }

    def fake_crop_ocr(image_bytes: bytes, page_index: int, x: float, y: float, w: float, h: float) -> dict:
        return {
            "ocr_available": True,
            "block_id": f"p{page_index}_crop",
            "text": "不思議な風景",
            "tokens": tokenize_plain_text("不思議な風景"),
            "box_pct": [round(x * 100, 3), round(y * 100, 3), round(w * 100, 3), round(h * 100, 3)],
        }

    monkeypatch.setattr(manga_ocr_service, "run_page_ocr", fake_page_ocr)
    monkeypatch.setattr(manga_ocr_service, "run_crop_ocr", fake_crop_ocr)
    monkeypatch.setattr(manga_ocr_service, "is_ocr_available", lambda: True)

    with TestClient(app) as client:
        # Cross-scan isolation: scanning novels must keep manga; scanning manga must keep novels
        assert len(books_service.scan_books_folder()) == 3
        assert len(manga_service.scan_manga_folder()) == 2
        assert len(books_service.scan_books_folder()) == 3
        assert len(manga_service.scan_manga_folder()) == 2

        all_books = client.get("/api/books").json()["books"]
        novels = [b for b in all_books if b["media_type"] == "novel"]
        mangas = [b for b in all_books if b["media_type"] == "manga"]
        assert len(novels) == 1
        assert len(mangas) == 2

        epub_manga_item = next(m for m in mangas if m["title"] == "サンプル漫画 第一巻")
        assert epub_manga_item["cover_url"] == f"/api/manga/{epub_manga_item['id']}/pages/0/image"
        assert epub_manga_item["page_count"] == 3

        manga_list_res = client.get("/api/manga")
        assert manga_list_res.status_code == 200
        assert manga_list_res.json()["ocr_available"] is True
        assert len(manga_list_res.json()["manga"]) == 2

        detail = client.get(f"/api/manga/{epub_manga_item['id']}").json()
        assert detail["media_type"] == "manga"
        assert len(detail["pages"]) == 3
        assert detail["pages"][2]["is_spread"] is True
        assert detail["pages"][2]["width"] == 1600
        assert detail["pages"][2]["height"] == 1200

        # Image streaming check
        img_resp = client.get(f"/api/manga/{epub_manga_item['id']}/pages/2/image")
        assert img_resp.status_code == 200
        assert img_resp.headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(img_resp.content)) as im:
            assert im.size == (1600, 1200)

        # Page OCR call 1 runs OCR and caches in SQLite; call 2 serves from cache
        ocr1 = client.post(f"/api/manga/{epub_manga_item['id']}/pages/1/ocr", json={}).json()
        assert ocr1["ocr_available"] is True
        assert ocr1["cached"] is False
        assert ocr_calls == 1
        assert len(ocr1["blocks"]) == 2
        assert ocr1["blocks"][1]["block_id"] == "p1_b1"

        ocr2 = client.post(f"/api/manga/{epub_manga_item['id']}/pages/1/ocr", json={}).json()
        assert ocr2["cached"] is True
        assert ocr_calls == 1

        # Crop OCR + tokenize endpoint
        crop_res = client.post(
            f"/api/manga/{epub_manga_item['id']}/pages/1/ocr-crop",
            json={"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.25},
        ).json()
        assert crop_res["text"] == "不思議な風景"
        assert any(t["lemma"] == "風景" for t in crop_res["tokens"])

        tok_res = client.post("/api/manga/tokenize", json={"text": "静かな朝"}).json()
        assert "".join(t["surface"] for t in tok_res["tokens"]) == "静かな朝"

        # Grammar & quiz context fallbacks read cached manga_pages OCR blocks
        target_sent, surrounding = grammar_service._load_sentence_context(
            epub_manga_item["id"], 1, "p1_b1"
        )
        assert target_sent == "駅前の商店街へ行こう。"
        assert "今日はいい天気だね！" in surrounding

        manga_sents = quiz_service._load_chapter_sentences(epub_manga_item["id"], 1)
        assert [s["text"] for s in manga_sents] == [
            "今日はいい天気だね！",
            "駅前の商店街へ行こう。",
        ]
    db.set_db_path(None)


def test_natural_volume_ordering_and_versioned_static_assets(tmp_path: Path, monkeypatch):
    """Verify full-width numeric volume ordering and versioned no-store static asset serving."""
    books_dir = tmp_path / "books"
    manga_dir = tmp_path / "manga"
    books_dir.mkdir()
    manga_dir.mkdir()

    # Deliberately create out-of-order filenames & full-width numerals (10, 2, 11, 1)
    for num_fw, fname in (("１０", "v10.epub"), ("２", "v2.epub"), ("１１", "v11.epub"), ("１", "v1.epub")):
        _create_synthetic_manga_epub(manga_dir / fname, title=f"テスト漫画 {num_fw}")

    test_db = tmp_path / "test_order_static.db"
    monkeypatch.setattr(config, "BOOKS_DIR", books_dir)
    monkeypatch.setattr(config, "MANGA_DIR", manga_dir)
    monkeypatch.setattr(config, "DB_PATH", test_db)
    db.set_db_path(test_db)
    db.init_db()

    try:
        with TestClient(app) as client:
            ordered_manga = client.get("/api/manga").json()["manga"]
            assert [m["title"] for m in ordered_manga] == [
                "テスト漫画 １",
                "テスト漫画 ２",
                "テスト漫画 １０",
                "テスト漫画 １１",
            ]

            ordered_books = client.get("/api/books").json()["books"]
            assert [b["title"] for b in ordered_books] == [
                "テスト漫画 １",
                "テスト漫画 ２",
                "テスト漫画 １０",
                "テスト漫画 １１",
            ]

            # GET / injects versioned CSS & JS URLs with no-store cache headers
            idx = client.get("/")
            assert idx.status_code == 200
            assert "no-store" in idx.headers.get("cache-control", "")
            assert 'id="manga-toc-select"' in idx.text
            assert 'id="btn-manga-exit-fs"' not in idx.text

            js_match = re.search(r'src="(/static/v/([a-f0-9]+)/js/main\.js)"', idx.text)
            css_match = re.search(r'href="(/static/v/([a-f0-9]+)/css/app\.css)"', idx.text)
            assert js_match is not None
            assert css_match is not None
            ver = js_match.group(2)
            assert css_match.group(2) == ver

            for asset_rel, expected_mime in (
                ("js/main.js", "javascript"),
                ("js/library.js", "javascript"),
                ("js/manga.js", "javascript"),
                ("css/app.css", "text/css"),
            ):
                r = client.get(f"/static/v/{ver}/{asset_rel}")
                assert r.status_code == 200
                assert expected_mime in r.headers.get("content-type", "")
                assert "no-store" in r.headers.get("cache-control", "")
                if asset_rel == "css/app.css":
                    assert ".manga-spread-group" in r.text

            # Traversal outside STATIC_DIR must 404
            assert client.get(f"/static/v/{ver}/%2e%2e/pyproject.toml").status_code == 404
            assert client.get(f"/static/v/{ver}/../pyproject.toml").status_code == 404
    finally:
        db.set_db_path(None)


def test_manga_toc_extraction_and_backfill(tmp_path: Path, monkeypatch):
    """Verify EPUB NCX TOC mapping, CBZ subfolder TOC, and lazy toc_json backfill preserving OCR."""
    books_dir = tmp_path / "books"
    manga_dir = tmp_path / "manga"
    books_dir.mkdir()
    manga_dir.mkdir()

    # Build a synthetic manga EPUB with toc.ncx (including Calibre-style unresolved entry 0)
    epub_path = manga_dir / "toc_manga.epub"
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    content_opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>目次テスト漫画 １</dc:title>
    <dc:creator>テスト</dc:creator>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="p0" href="text/p0.xhtml" media-type="application/xhtml+xml"/>
    <item id="p1" href="text/p1.xhtml" media-type="application/xhtml+xml"/>
    <item id="p2" href="text/p2.xhtml" media-type="application/xhtml+xml"/>
    <item id="img0" href="images/0.png" media-type="image/png"/>
    <item id="img1" href="images/1.png" media-type="image/png"/>
    <item id="img2" href="images/2.png" media-type="image/png"/>
  </manifest>
  <spine toc="ncx" page-progression-direction="rtl">
    <itemref idref="p0"/>
    <itemref idref="p1"/>
    <itemref idref="p2"/>
  </spine>
</package>"""
    toc_ncx = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="n0" playOrder="1"><navLabel><text>表紙</text></navLabel><content src="missing_cover_alias.xhtml"/></navPoint>
    <navPoint id="n1" playOrder="2"><navLabel><text>第1話</text></navLabel><content src="text/p1.xhtml"/></navPoint>
    <navPoint id="n2" playOrder="3"><navLabel><text>第2話</text></navLabel><content src="text/p2.xhtml"/></navPoint>
  </navMap>
</ncx>"""
    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/toc.ncx", toc_ncx)
        for i in range(3):
            zf.writestr(
                f"OEBPS/text/p{i}.xhtml",
                f'<html><body><div><img src="../images/{i}.png"/></div></body></html>',
            )
            zf.writestr(f"OEBPS/images/{i}.png", _make_solid_png_bytes(800, 1200))

    # Build a multi-folder CBZ archive
    cbz_path = manga_dir / "cbz_chapters.cbz"
    with zipfile.ZipFile(cbz_path, "w") as zf:
        zf.writestr("Ch01/001.png", _make_solid_png_bytes(800, 1200))
        zf.writestr("Ch01/002.png", _make_solid_png_bytes(800, 1200))
        zf.writestr("Ch02/001.png", _make_solid_png_bytes(800, 1200))

    assert extract_manga_toc(epub_path) == [
        {"title": "表紙", "page_index": 0},
        {"title": "第1話", "page_index": 1},
        {"title": "第2話", "page_index": 2},
    ]
    assert extract_manga_toc(cbz_path) == [
        {"title": "Ch01", "page_index": 0},
        {"title": "Ch02", "page_index": 2},
    ]

    test_db = tmp_path / "test_toc.db"
    monkeypatch.setattr(config, "BOOKS_DIR", books_dir)
    monkeypatch.setattr(config, "MANGA_DIR", manga_dir)
    monkeypatch.setattr(config, "DB_PATH", test_db)
    db.set_db_path(test_db)
    db.init_db()

    try:
        manga_service.scan_manga_folder()
        items = manga_service.list_manga()
        epub_id = next(m["id"] for m in items if m["title"] == "目次テスト漫画 １")

        # Simulate pre-upgrade row with cached OCR and NULL toc_json
        sentinel_ocr = json.dumps({"ocr_available": True, "blocks": []}, ensure_ascii=False)
        with db.get_conn() as conn:
            conn.execute("UPDATE books SET toc_json = NULL WHERE id = ?", (epub_id,))
            conn.execute(
                "UPDATE manga_pages SET ocr_json = ? WHERE book_id = ? AND page_index = 1",
                (sentinel_ocr, epub_id),
            )

        with TestClient(app) as client:
            detail = client.get(f"/api/manga/{epub_id}").json()
            assert detail["toc"] == [
                {"title": "表紙", "page_index": 0},
                {"title": "第1話", "page_index": 1},
                {"title": "第2話", "page_index": 2},
            ]

        # Backfill must preserve existing OCR cache and populate books.toc_json
        with db.get_conn() as conn:
            book_row = conn.execute("SELECT toc_json FROM books WHERE id = ?", (epub_id,)).fetchone()
            page_row = conn.execute(
                "SELECT ocr_json FROM manga_pages WHERE book_id = ? AND page_index = 1",
                (epub_id,),
            ).fetchone()
        assert book_row["toc_json"] is not None
        assert page_row["ocr_json"] == sentinel_ocr
    finally:
        db.set_db_path(None)


def test_hide_annotations_setting_and_assets(temp_env):
    """Verify hide_annotations setting persistence and clean-view UI/CSS markup."""
    with TestClient(app) as client:
        s_get = client.get("/api/settings")
        assert s_get.status_code == 200
        assert s_get.json()["hide_annotations"] == "false"

        s_put = client.put("/api/settings", json={"hide_annotations": "true"})
        assert s_put.status_code == 200
        assert s_put.json()["hide_annotations"] == "true"

        idx = client.get("/")
        assert idx.status_code == 200
        assert 'id="btn-toggle-highlights"' in idx.text
        assert 'id="btn-manga-hide-marks"' in idx.text

        css_match = re.search(r'href="(/static/v/[a-f0-9]+/css/app\.css)"', idx.text)
        assert css_match is not None
        css_res = client.get(css_match.group(1))
        assert css_res.status_code == 200
        assert 'html[data-hide-annotations="true"] .manga-ocr-layer' in css_res.text
        assert 'html[data-hide-annotations="true"] .sent.has-note' in css_res.text


def test_multicolor_highlights_and_inline_note_filtering(temp_env):
    with TestClient(app) as client:
        books = client.get("/api/books").json()["books"]
        book1 = books[0]
        ch1_idx = next(c["chapter_index"] for c in book1["chapters"] if c["title"] == "１")

        # 1) Create highlights across all supported colors + None ("") + invalid fallback
        created = {}
        for color in ("yellow", "blue", "pink", "orange", "green", "purple", ""):
            res = client.post(
                "/api/notes",
                json={
                    "book_id": book1["id"],
                    "chapter_index": ch1_idx,
                    "sent_id": f"c{ch1_idx}_s1",
                    "quote_text": f"引用（{color or 'plain'}）",
                    "note_text": f"Body for {color or 'plain'}",
                    "category": "quote" if color else "note",
                    "color": color,
                },
            )
            assert res.status_code == 200
            note = res.json()["note"]
            assert note["color"] == color
            assert note["media_type"] == "novel"
            created[color] = note

        fallback_res = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "quote_text": "フォールバック引用",
                "color": "neon-invalid",
            },
        )
        assert fallback_res.status_code == 200
        assert fallback_res.json()["note"]["color"] == "yellow"

        # 2) Inline ==[pink]...== inside note_text and quote_text
        inline_note_res = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "quote_text": "Plain quote",
                "note_text": "Remember ==[pink]key phrase== in this scene.",
                "category": "note",
                "color": "blue",
            },
        )
        assert inline_note_res.status_code == 200
        inline_note = inline_note_res.json()["note"]

        inline_quote_res = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "quote_text": "Contains ==[pink]highlighted quote== inside.",
                "note_text": "Commentary",
                "category": "expression",
                "color": "green",
            },
        )
        assert inline_quote_res.status_code == 200
        inline_quote = inline_quote_res.json()["note"]

        # 3) PUT update color + inline ==[purple]...== markup
        upd_res = client.put(
            f"/api/notes/{created['']['id']}",
            json={
                "color": "orange",
                "note_text": "Updated with ==[purple]violet emphasis== inside.",
            },
        )
        assert upd_res.status_code == 200
        updated_note = upd_res.json()["note"]
        assert updated_note["color"] == "orange"
        assert "==[purple]violet emphasis==" in updated_note["note_text"]

        # 4) Filter by color matches card color AND inline ==[color]...== markup
        pink_notes = client.get(
            f"/api/notes?book_id={book1['id']}&color=pink"
        ).json()["notes"]
        pink_ids = {n["id"] for n in pink_notes}
        assert created["pink"]["id"] in pink_ids
        assert inline_note["id"] in pink_ids
        assert inline_quote["id"] in pink_ids
        assert created["blue"]["id"] not in pink_ids
        assert created["green"]["id"] not in pink_ids

        purple_notes = client.get(
            f"/api/notes?book_id={book1['id']}&color=purple"
        ).json()["notes"]
        purple_ids = {n["id"] for n in purple_notes}
        assert created["purple"]["id"] in purple_ids
        assert updated_note["id"] in purple_ids

        # 5) Chapter endpoint exposes chapter_notes with highlight colors for fast re-rendering
        chap_payload = client.get(f"/api/books/{book1['id']}/chapters/{ch1_idx}").json()
        chap_notes = chap_payload["chapter_notes"]
        by_id = {n["id"]: n for n in chap_notes}
        assert by_id[created["pink"]["id"]]["color"] == "pink"
        assert by_id[created["pink"]["id"]]["sent_id"] == f"c{ch1_idx}_s1"
        assert by_id[created["pink"]["id"]]["quote_text"] == "引用（pink）"
        assert by_id[updated_note["id"]]["color"] == "orange"

        # 6) Frontend templates & CSS include multi-color highlight controls
        index_res = client.get("/")
        assert index_res.status_code == 200
        assert "hl-swatch-btn" in index_res.text
        assert "note-color-filter-btn" in index_res.text
        assert 'id="note-hl-popover"' in index_res.text

        css_res = client.get("/static/css/app.css")
        assert css_res.status_code == 200
        assert "--hl-purple-solid" in css_res.text
        assert "mark.user-hl.hl-pink" in css_res.text
        assert 'html[data-hide-annotations="true"] mark.user-hl' in css_res.text


def test_manga_single_page_horizontal_layout_setting_and_assets(temp_env):
    with TestClient(app) as client:
        res_single = client.put("/api/settings", json={"manga_layout": "single"})
        assert res_single.status_code == 200
        assert res_single.json()["manga_layout"] == "single"

        fetched = client.get("/api/settings")
        assert fetched.status_code == 200
        assert fetched.json()["manga_layout"] == "single"

        res_vertical = client.put("/api/settings", json={"manga_layout": "vertical"})
        assert res_vertical.status_code == 200
        assert res_vertical.json()["manga_layout"] == "vertical"

        res_invalid = client.put("/api/settings", json={"manga_layout": "invalid-mode"})
        assert res_invalid.status_code == 200
        assert res_invalid.json()["manga_layout"] == "spread"

        manga_js = client.get("/static/js/manga.js")
        assert manga_js.status_code == 200
        assert "1-Page Horizontal" in manga_js.text
        assert "mode-single" in manga_js.text

        css_res = client.get("/static/css/app.css")
        assert css_res.status_code == 200
        assert ".manga-stage.mode-single" in css_res.text

        index_res = client.get("/")
        assert index_res.status_code == 200
        assert "Cycle 2-Page Spread / 1-Page Horizontal / 1-Page Vertical (V)" in index_res.text


def test_highlight_recolor_dedup_and_multisentence_assets(temp_env):
    with TestClient(app) as client:
        books = client.get("/api/books").json()["books"]
        book1 = books[0]
        ch1_idx = next(c["chapter_index"] for c in book1["chapters"] if c["title"] == "１")

        # 1) Create multi-paragraph highlight with replace_existing=True
        r1 = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s1",
                "quote_text": "文一。\n文二。",
                "color": "yellow",
                "replace_existing": True,
            },
        )
        assert r1.status_code == 200
        hl_id = r1.json()["note"]["id"]
        assert r1.json()["note"]["color"] == "yellow"

        # 2) Recolor yellow -> pink with whitespace variation updates the same highlight row
        r2 = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s1",
                "quote_text": "文一。文二。",
                "color": "pink",
                "replace_existing": True,
            },
        )
        assert r2.status_code == 200
        assert r2.json()["note"]["id"] == hl_id
        assert r2.json()["note"]["color"] == "pink"
        notes_after_pink = [
            n
            for n in client.get(f"/api/notes?book_id={book1['id']}&chapter_index={ch1_idx}").json()["notes"]
            if "文一" in n["quote_text"]
        ]
        assert len(notes_after_pink) == 1

        # 3) Recolor pink -> yellow updates cleanly back to yellow with no duplicate rows
        r3 = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s1",
                "quote_text": "文一。\n文二。",
                "color": "yellow",
                "replace_existing": True,
            },
        )
        assert r3.status_code == 200
        assert r3.json()["note"]["id"] == hl_id
        assert r3.json()["note"]["color"] == "yellow"
        notes_after_yellow = [
            n
            for n in client.get(f"/api/notes?book_id={book1['id']}&chapter_index={ch1_idx}").json()["notes"]
            if "文一" in n["quote_text"]
        ]
        assert len(notes_after_yellow) == 1

        # 4) Legacy duplicate empty row + annotated note: keep annotated note and prune empty duplicate
        dup_empty = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "quote_text": "重複チェック用の文。",
                "color": "pink",
                "replace_existing": False,
            },
        )
        assert dup_empty.status_code == 200
        empty_dup_id = dup_empty.json()["note"]["id"]

        annotated = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "quote_text": "重複チェック用の文。",
                "note_text": "keep me",
                "color": "pink",
                "replace_existing": False,
            },
        )
        assert annotated.status_code == 200
        annotated_id = annotated.json()["note"]["id"]

        cleaned = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "quote_text": "重複チェック用の文。",
                "color": "yellow",
                "replace_existing": True,
            },
        )
        assert cleaned.status_code == 200
        kept_note = cleaned.json()["note"]
        assert kept_note["id"] == annotated_id
        assert kept_note["note_text"] == "keep me"
        assert kept_note["color"] == "yellow"

        all_chap_ids = {
            n["id"]
            for n in client.get(f"/api/notes?book_id={book1['id']}&chapter_index={ch1_idx}").json()["notes"]
        }
        assert annotated_id in all_chap_ids
        assert empty_dup_id not in all_chap_ids

        # 5) chapter_notes ordered updated_at ASC, id ASC so later updates win
        note_a = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s2",
                "quote_text": "順序確認の文。",
                "color": "yellow",
            },
        ).json()["note"]
        note_b = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s2",
                "quote_text": "順序確認の文。",
                "color": "pink",
            },
        ).json()["note"]
        upd_a = client.put(f"/api/notes/{note_a['id']}", json={"color": "yellow"})
        assert upd_a.status_code == 200

        chap_notes = client.get(f"/api/books/{book1['id']}/chapters/{ch1_idx}").json()["chapter_notes"]
        ordered_ids = [n["id"] for n in chap_notes]
        assert ordered_ids.index(note_b["id"]) < ordered_ids.index(note_a["id"])

        # 6) Static JS bundles serve non-nesting & multi-paragraph highlight code
        state_js = client.get("/static/js/state.js")
        assert state_js.status_code == 200
        assert "compactDoc" in state_js.text
        assert "anchorEl" in state_js.text

        reader_js = client.get("/static/js/reader.js")
        assert reader_js.status_code == 200
        assert "replace_existing: true" in reader_js.text


def test_highlight_dedup_existing_notes_and_unhighlight(temp_env):
    with TestClient(app) as client:
        books = client.get("/api/books").json()["books"]
        book1 = books[0]
        ch1_idx = next(c["chapter_index"] for c in book1["chapters"] if c["title"] == "１")

        # 1) Existing note with written commentary & edge punctuation + empty color
        saved_note = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s1",
                "quote_text": "「駅前の商店街。」",
                "note_text": "Already saved with my study note.",
                "category": "expression",
                "color": "",
            },
        ).json()["note"]

        # Highlighting the exact core (without punctuation) or a contained substring updates in place
        rehl_core = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s1",
                "quote_text": "駅前の商店街",
                "color": "blue",
                "replace_existing": True,
            },
        )
        assert rehl_core.status_code == 200
        assert rehl_core.json()["note"]["id"] == saved_note["id"]
        assert rehl_core.json()["note"]["color"] == "blue"
        assert rehl_core.json()["note"]["note_text"] == "Already saved with my study note."

        rehl_sub = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s1",
                "quote_text": "商店街",
                "color": "pink",
                "replace_existing": True,
            },
        )
        assert rehl_sub.status_code == 200
        assert rehl_sub.json()["note"]["id"] == saved_note["id"]
        assert rehl_sub.json()["note"]["color"] == "pink"
        assert rehl_sub.json()["note"]["quote_text"] == "「駅前の商店街。」"

        # Highlighting by DOM target_note_ids updates in place without duplicating
        rehl_id = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "quote_text": "商店街",
                "color": "green",
                "replace_existing": True,
                "target_note_ids": [saved_note["id"]],
            },
        )
        assert rehl_id.status_code == 200
        assert rehl_id.json()["note"]["id"] == saved_note["id"]
        assert rehl_id.json()["note"]["color"] == "green"

        chap_notes = client.get(
            f"/api/notes?book_id={book1['id']}&chapter_index={ch1_idx}"
        ).json()["notes"]
        matching_saved = [n for n in chap_notes if "商店街" in n["quote_text"]]
        assert len(matching_saved) == 1

        # 2) Unhighlight entry WITHOUT a written note -> deletes row from notes
        hl_only = client.post(
            "/api/notes",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s2",
                "quote_text": "消去されるハイライト。",
                "note_text": "",
                "color": "yellow",
                "replace_existing": True,
            },
        ).json()["note"]
        unhl_empty = client.post(
            "/api/notes/unhighlight",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "sent_id": f"c{ch1_idx}_s2",
                "quote_text": "消去されるハイライト",
                "note_ids": [hl_only["id"]],
            },
        )
        assert unhl_empty.status_code == 200
        unhl_empty_data = unhl_empty.json()
        assert hl_only["id"] in unhl_empty_data["deleted_ids"]
        assert unhl_empty_data["cleared_ids"] == []
        remaining_ids = {
            n["id"]
            for n in client.get(f"/api/notes?book_id={book1['id']}&chapter_index={ch1_idx}").json()["notes"]
        }
        assert hl_only["id"] not in remaining_ids

        # 3) Unhighlight entry WITH a written note -> keeps row, clears color to ""
        unhl_written = client.post(
            "/api/notes/unhighlight",
            json={
                "book_id": book1["id"],
                "chapter_index": ch1_idx,
                "note_ids": [saved_note["id"]],
            },
        )
        assert unhl_written.status_code == 200
        unhl_written_data = unhl_written.json()
        assert saved_note["id"] in unhl_written_data["cleared_ids"]
        assert unhl_written_data["deleted_ids"] == []
        refetched = [
            n
            for n in client.get(f"/api/notes?book_id={book1['id']}&chapter_index={ch1_idx}").json()["notes"]
            if n["id"] == saved_note["id"]
        ]
        assert len(refetched) == 1
        assert refetched[0]["color"] == ""
        assert refetched[0]["note_text"] == "Already saved with my study note."

        # 4) Frontend templates & JS include unhighlight controls & target_note_ids wiring
        index_res = client.get("/")
        assert index_res.status_code == 200
        assert 'id="btn-sel-unhighlight"' in index_res.text
        assert 'id="btn-manga-unhighlight"' in index_res.text

        api_js = client.get("/static/js/api.js")
        assert api_js.status_code == 200
        assert "unhighlightNotes" in api_js.text

        reader_js = client.get("/static/js/reader.js")
        assert reader_js.status_code == 200
        assert "target_note_ids" in reader_js.text

        css_res = client.get("/static/css/app.css")
        assert css_res.status_code == 200
        assert ".trouble-row .trouble-word {" in css_res.text
        assert "\n.trouble-word {" not in css_res.text
        assert not any(line.strip().startswith(".trouble-word {") for line in css_res.text.splitlines())


def test_async_llamacpp_startup_status_and_manga_screenshot(
    temp_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # 1) run.py must not block synchronously on ensure_managed_server_running before uvicorn.run
    run_src = (Path(__file__).resolve().parent.parent / "run.py").read_text(encoding="utf-8")
    assert "ensure_managed_server_running" not in run_src

    async_calls: list[dict[str, str]] = []
    orig_async_start = llamacpp_manager.start_managed_server_async

    def spy_async_start(settings_dict, *args, **kwargs):
        async_calls.append(dict(settings_dict))
        return orig_async_start(settings_dict, *args, **kwargs)

    monkeypatch.setattr(llamacpp_manager, "start_managed_server_async", spy_async_start)

    _create_synthetic_manga_epub(config.MANGA_DIR / "shot_sample.epub", title="サンプル漫画 第一巻")

    with TestClient(app) as client:
        assert len(async_calls) >= 1

        # 2) Non-LLM setting updates must not block or restart llama-server
        ensure_calls: list[bool] = []
        stop_calls: list[bool] = []
        monkeypatch.setattr(
            llamacpp_manager,
            "ensure_managed_server_running",
            lambda *a, **kw: ensure_calls.append(True) or True,
        )
        monkeypatch.setattr(
            llamacpp_manager,
            "stop_managed_server",
            lambda: stop_calls.append(True),
        )
        non_llm = client.put("/api/settings", json={"theme": "dark", "manga_layout": "single"})
        assert non_llm.status_code == 200
        assert ensure_calls == []
        assert stop_calls == []

        # 3) /api/llm/status reports loading, ready, stopped, external, and gemini states cleanly
        monkeypatch.setattr(llamacpp_manager, "is_server_ready", lambda *a, **kw: False)
        monkeypatch.setattr(llamacpp_manager, "_STARTING", True)
        monkeypatch.setattr(llamacpp_manager, "_MANAGED_MODEL", "qwen-test")
        st_loading = client.get("/api/llm/status")
        assert st_loading.status_code == 200
        assert st_loading.json()["status"] == "loading"
        assert st_loading.json()["model"] == "qwen-test"

        monkeypatch.setattr(llamacpp_manager, "_STARTING", False)
        monkeypatch.setattr(llamacpp_manager, "is_server_ready", lambda *a, **kw: True)
        st_ready = client.get("/api/llm/status")
        assert st_ready.status_code == 200
        assert st_ready.json()["status"] == "ready"

        monkeypatch.setattr(llamacpp_manager, "is_server_ready", lambda *a, **kw: False)
        st_stopped = client.get("/api/llm/status")
        assert st_stopped.status_code == 200
        assert st_stopped.json()["status"] == "stopped"

        client.put("/api/settings", json={"llm_provider": "ollama", "ollama_model": "qwen3:8b"})
        st_ollama = client.get("/api/llm/status").json()
        assert st_ollama["provider"] == "ollama"
        assert st_ollama["status"] == "external"

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        client.put("/api/settings", json={"llm_provider": "gemini", "clear_gemini_key": True})
        st_gem = client.get("/api/llm/status").json()
        assert st_gem["provider"] == "gemini"
        assert st_gem["status"] == "unconfigured"

        # 4) Manga lossless PNG screenshot endpoint (1-page & 2-page spread stitching)
        scan_res = client.post("/api/manga/scan")
        assert scan_res.status_code == 200
        manga_list = scan_res.json()["manga"]
        assert len(manga_list) == 1
        manga_id = manga_list[0]["id"]

        shot_single = client.get(f"/api/manga/{manga_id}/screenshot?pages=0")
        assert shot_single.status_code == 200
        assert shot_single.headers["content-type"].startswith("image/png")
        assert "filename*=UTF-8''" in shot_single.headers.get("content-disposition", "")
        with Image.open(io.BytesIO(shot_single.content)) as im1:
            assert im1.size == (800, 1200)

        shot_spread = client.get(f"/api/manga/{manga_id}/screenshot?pages=1,0")
        assert shot_spread.status_code == 200
        assert shot_spread.headers["content-type"].startswith("image/png")
        assert "p001-002" in shot_spread.headers.get("content-disposition", "")
        with Image.open(io.BytesIO(shot_spread.content)) as im2:
            assert im2.size == (1600, 1200)

        for bad_pages in ("", "0,1,2", "abc", "-1"):
            assert (
                client.get(f"/api/manga/{manga_id}/screenshot?pages={bad_pages}").status_code
                == 400
            )
        assert client.get(f"/api/manga/{manga_id}/screenshot?pages=99").status_code == 404

        # 5) Frontend includes background LLM status pill and screenshot UI
        index_res = client.get("/")
        assert index_res.status_code == 200
        assert 'id="btn-llm-status"' in index_res.text
        assert 'id="btn-manga-screenshot"' in index_res.text

        api_js = client.get("/static/js/api.js")
        assert api_js.status_code == 200
        assert "getLLMStatus" in api_js.text

        manga_js = client.get("/static/js/manga.js")
        assert manga_js.status_code == 200
        assert "/screenshot?pages=" in manga_js.text
        assert "\nfunction hideShotPopover()" in manga_js.text

        manga_bar_start = index_res.text.index('id="manga-bar"')
        manga_bar_end = index_res.text.index("</header>", manga_bar_start)
        shot_pop_pos = index_res.text.index('id="manga-shot-popover"')
        assert shot_pop_pos > manga_bar_end

        css_res = client.get("/static/css/app.css")
        assert css_res.status_code == 200
        assert ".manga-shot-popover {\n  position: fixed;" in css_res.text.replace("\r\n", "\n")


def test_mobile_phase1_lan_launcher_pwa_and_touch_assets(temp_env):
    import importlib

    run_mod = importlib.import_module("run")
    args_default = run_mod.parse_args([])
    assert (args_default.host, args_default.port) == ("127.0.0.1", 8000)

    args_lan = run_mod.parse_args(["--lan", "--port", "8088"])
    assert (args_lan.host, args_lan.port) == ("0.0.0.0", 8088)

    with TestClient(app) as client:
        manifest_res = client.get("/static/manifest.webmanifest")
        assert manifest_res.status_code == 200
        assert "application/manifest+json" in manifest_res.headers.get("content-type", "")
        manifest_data = manifest_res.json()
        assert manifest_data["display"] == "standalone"
        assert manifest_data["short_name"] == "Shiori"

        icon_res = client.get("/static/icon.svg")
        assert icon_res.status_code == 200
        assert "image/svg+xml" in icon_res.headers.get("content-type", "")
        assert "栞" in icon_res.text

        index_res = client.get("/")
        assert index_res.status_code == 200
        assert "viewport-fit=cover" in index_res.text
        assert "/static/manifest.webmanifest" in index_res.text
        assert 'autocorrect="off" autocapitalize="none" spellcheck="false"' in index_res.text

        reader_js = client.get("/static/js/reader.js")
        assert reader_js.status_code == 200
        assert "selectionchange" in reader_js.text
        assert "touch-docked" in reader_js.text
        assert "pointer: coarse" in reader_js.text

        notes_js = client.get("/static/js/notes.js")
        assert notes_js.status_code == 200
        assert "selectionchange" in notes_js.text
        assert "touch-docked" in notes_js.text

        lookup_js = client.get("/static/js/lookup.js")
        assert lookup_js.status_code == 200
        assert '"pointerdown"' in lookup_js.text
        assert "window.innerWidth <= 768" in lookup_js.text

        manga_js = client.get("/static/js/manga.js")
        assert manga_js.status_code == 200
        assert "shiori_mobile_manga_layout" in manga_js.text
        assert "mobile-chrome-hidden" in manga_js.text
        assert '"touchstart"' in manga_js.text

        css_res = client.get("/static/css/app.css")
        assert css_res.status_code == 200
        assert "safe-area-inset-bottom" in css_res.text
        assert ".selection-tooltip.touch-docked" in css_res.text
        assert "html.mobile-chrome-hidden #manga-bar" in css_res.text


def test_translate_in_context_screenshot_copy_and_lan_banner(temp_env, monkeypatch):
    import importlib

    run_mod = importlib.import_module("run")
    banner_lan = run_mod.format_startup_banner("0.0.0.0", 8000, lan_ips=["192.168.1.198"])
    assert "Open on your phone (same Wi-Fi): http://192.168.1.198:8000" in banner_lan
    banner_local = run_mod.format_startup_banner("127.0.0.1", 8000, lan_ips=[])
    assert "uv run python run.py --lan" in banner_local
    ips = run_mod.detect_lan_ips()
    assert isinstance(ips, list)

    _build_sample_epub(config.BOOKS_DIR / "translate_test.epub")

    captured_calls: list[tuple[str, list[dict[str, str]]]] = []

    async def fake_generate_text(*, settings, system_prompt, messages, expect_json=False):
        captured_calls.append((system_prompt, messages))
        if "translator" in system_prompt.lower():
            return ("It means she hurried toward the station in the morning breeze.", "llamacpp", "test-model")
        return ("**Meaning in context:** Grammar breakdown response.", "llamacpp", "test-model")

    monkeypatch.setattr(llm, "generate_text", fake_generate_text)

    with TestClient(app) as client:
        books_res = client.post("/api/books/scan")
        assert books_res.status_code == 200
        book1 = books_res.json()["books"][0]
        book_id = book1["id"]
        ch1_idx = next(c["chapter_index"] for c in book1["chapters"] if c["title"] == "１")
        chap = client.get(f"/api/books/{book_id}/chapters/{ch1_idx}").json()
        assert chap["chapter_index"] == ch1_idx
        sent_id = f"c{ch1_idx}_s0"
        sent_text = "駅前にはお店が立ち並ぶ商店街がある。"

        # 1) Contextual translation mode
        t1 = client.post(
            "/api/grammar/ask",
            json={
                "book_id": book_id,
                "chapter_index": ch1_idx,
                "sent_id": sent_id,
                "target_text": "お店が立ち並ぶ",
                "sentence_text": sent_text,
                "question": "",
                "mode": "translate",
            },
        )
        assert t1.status_code == 200
        d1 = t1.json()
        assert d1["llm_unavailable"] is False
        assert d1["reused"] is False
        assert "hurried toward the station" in d1["thread"]["messages"][-1]["content"]
        assert d1["thread"]["messages"][-1]["mode"] == "translate"
        assert "[Surrounding Context]" in captured_calls[-1][1][0]["content"]
        assert "translator" in captured_calls[-1][0].lower()

        # Re-clicking Translate without a follow-up question reuses the translate answer
        t2 = client.post(
            "/api/grammar/ask",
            json={
                "book_id": book_id,
                "chapter_index": ch1_idx,
                "sent_id": sent_id,
                "target_text": "お店が立ち並ぶ",
                "sentence_text": sent_text,
                "question": "",
                "mode": "translate",
            },
        )
        assert t2.status_code == 200
        assert t2.json()["reused"] is True
        assert len(captured_calls) == 1

        # Clicking ? Grammar on the same phrase runs grammar mode rather than returning translate-only reuse
        g1 = client.post(
            "/api/grammar/ask",
            json={
                "book_id": book_id,
                "chapter_index": ch1_idx,
                "sent_id": sent_id,
                "target_text": "お店が立ち並ぶ",
                "sentence_text": sent_text,
                "question": "",
                "mode": "grammar",
            },
        )
        assert g1.status_code == 200
        gd1 = g1.json()
        assert gd1["reused"] is False
        assert gd1["thread"]["messages"][-1]["mode"] == "grammar"
        assert "Grammar breakdown response" in gd1["thread"]["messages"][-1]["content"]
        assert len(captured_calls) == 2

        # 2) Frontend buttons & Right/Left page copy controls
        index_res = client.get("/")
        assert index_res.status_code == 200
        assert 'id="btn-sel-translate"' in index_res.text
        assert 'id="btn-manga-bar-translate"' in index_res.text

        manga_js = client.get("/static/js/manga.js")
        assert manga_js.status_code == 200
        assert "Right Page" in manga_js.text
        assert "Left Page" in manga_js.text
        assert "copyToClipboard: true" in manga_js.text
        assert "btn-manga-bar-translate" in manga_js.text

        reader_js = client.get("/static/js/reader.js")
        assert reader_js.status_code == 200
        assert "btn-sel-translate" in reader_js.text
        assert 'mode: "translate"' in reader_js.text

        grammar_js = client.get("/static/js/grammar.js")
        assert grammar_js.status_code == 200
        assert "Translating in context" in grammar_js.text


def test_mobile_manga_toolbar_scrubber_timer_and_refresh_restore(temp_env):
    with TestClient(app) as client:
        index_res = client.get("/")
        assert index_res.status_code == 200
        html = index_res.text

        # 1) Timer popover is detached from <header class="reader-bar"> so it never clips inside horizontal scroll containers
        reader_bar_start = html.index('class="reader-bar"')
        reader_bar_end = html.index("</header>", reader_bar_start)
        timer_pop_pos = html.index('id="timer-popover"')
        assert timer_pop_pos > reader_bar_end

        # 2) Manga page scrubber / scrollbar controls are present
        assert 'id="manga-scrubber-bar"' in html
        assert 'id="manga-page-slider"' in html

        # 3) CSS fixes: no overlap in mobile toolbars, touch OCR boxes visible, crop disables box interception, pull-to-refresh contained
        css_res = client.get("/static/css/app.css")
        assert css_res.status_code == 200
        css = css_res.text
        assert "overscroll-behavior-y: contain;" in css
        assert "flex: 1 0 100%;" in css
        assert ".manga-viewport.crop-active .manga-ocr-box" in css
        assert "@media (hover: none), (pointer: coarse)" in css
        assert "html.sel-tooltip-docked .manga-text-bar" in css
        assert ".manga-scrubber-bar" in css

        # 4) JS fixes: pointerdown + dynamic positioning on timer, hash restore on refresh, manga scrubber & pointercancel
        timer_js = client.get("/static/js/timer.js").text
        assert '"pointerdown"' in timer_js
        assert "positionTimerPopover" in timer_js

        main_js = client.get("/static/js/main.js").text
        assert "restoreViewFromHash" in main_js
        assert "#book\\/" in main_js
        assert "#manga\\/" in main_js

        reader_js = client.get("/static/js/reader.js").text
        assert "history.replaceState" in reader_js
        assert "pagehide" in reader_js
        assert "sel-tooltip-docked" in reader_js

        library_js = client.get("/static/js/library.js").text
        assert "history.replaceState" in library_js

        manga_js = client.get("/static/js/manga.js").text
        assert "manga-page-slider" in manga_js
        assert "pointercancel" in manga_js
        assert "history.replaceState" in manga_js
        assert "pagehide" in manga_js






