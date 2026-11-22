"""End-to-end tests using synthetic EPUB data, SudachiPy, Jamdict, and FastAPI TestClient."""

from __future__ import annotations

import base64
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app import config, db, dictionary, llm
from app.epub_parser import parse_epub, read_epub_asset
from app.main import app
from app.tokenizer import (
    chunk_into_bunsetsu,
    extract_particle_quiz,
    tokenize_chapter_html,
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

    test_db = tmp_path / "test_app.db"
    monkeypatch.setattr(config, "BOOKS_DIR", books_dir)
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

    async def fake_generate_text(*, settings, system_prompt, messages, expect_json=False):
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
    async def fake_generate_text(*, settings, system_prompt, messages, expect_json=False):
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

        kinds = {q["kind"] for q in questions}
        assert "cloze" in kinds
        assert "scramble" in kinds

        for q in questions:
            if q["kind"] == "scramble":
                assert q["chunks"] != q["correct_answer"]
                assert sorted(q["chunks"]) == sorted(q["correct_answer"])
            else:
                assert "＿＿" in q["blanked_sentence"]
                assert len(q["choices"]) == 4
                assert q["correct_answer"] in q["choices"]

        # Submit quiz with all answers correct -> lowers scores & retires low-score word
        submit_results = [
            {
                "question_id": q["question_id"],
                "kind": q["kind"],
                "trouble_item_id": q["trouble_item_id"],
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
        assert any(c["display_text"] == "透き通る" and c["retired"] for c in sub_res["conquered"])

        # Retired item disappears from default active /api/trouble, remains under include_retired=true
        active_after = client.get(f"/api/trouble?book_id={book1['id']}").json()["items"]
        assert all(i["item_key"] != "透き通る|すきとおる" for i in active_after)

        all_after = client.get(
            f"/api/trouble?book_id={book1['id']}&include_retired=true"
        ).json()["items"]
        retired_word = next(i for i in all_after if i["item_key"] == "透き通る|すきとおる")
        assert retired_word["retired"] == 1

        # Manual retire / restore toggle
        restored = client.post(
            f"/api/trouble/{retired_word['id']}/retire",
            json={"retired": False},
        ).json()["item"]
        assert restored["retired"] == 0

        # Quiz history persisted
        hist = client.get(f"/api/quiz/history?book_id={book1['id']}").json()["sessions"]
        assert len(hist) == 1
        assert hist[0]["correct_count"] == 5
        assert hist[0]["total_questions"] == 5
