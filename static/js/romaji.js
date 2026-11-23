/** Lightweight zero-dependency Hepburn/Kunrei romaji-to-hiragana converter for active recall input. */

const ROMAJI_TABLE = {
  kya: "きゃ", kyu: "きゅ", kyo: "きょ",
  gya: "ぎゃ", gyu: "ぎゅ", gyo: "ぎょ",
  sha: "しゃ", shi: "し", shu: "しゅ", sho: "しょ",
  sya: "しゃ", syu: "しゅ", syo: "しょ",
  ja: "じゃ", ji: "じ", ju: "じゅ", jo: "じょ",
  jya: "じゃ", jyu: "じゅ", jyo: "じょ",
  zya: "じゃ", zyu: "じゅ", zyo: "じょ",
  cha: "ちゃ", chi: "ち", chu: "ちゅ", cho: "ちょ",
  tya: "ちゃ", tyu: "ちゅ", tyo: "ちょ",
  cya: "ちゃ", cyu: "ちゅ", cyo: "ちょ",
  nya: "にゃ", nyu: "にゅ", nyo: "にょ",
  hya: "ひゃ", hyu: "ひゅ", hyo: "ひょ",
  bya: "びゃ", byu: "びゅ", byo: "びょ",
  pya: "ぴゃ", pyu: "ぴゅ", pyo: "ぴょ",
  mya: "みゃ", myu: "みゅ", myo: "みょ",
  rya: "りゃ", ryu: "りゅ", ryo: "りょ",
  tsu: "つ",
  dzu: "づ", dji: "ぢ",
  ka: "か", ki: "き", ku: "く", ke: "け", ko: "こ",
  ga: "が", gi: "ぎ", gu: "ぐ", ge: "げ", go: "ご",
  sa: "さ", si: "し", su: "す", se: "せ", so: "そ",
  za: "ざ", zi: "じ", zu: "ず", ze: "ぜ", zo: "ぞ",
  ta: "た", ti: "ち", tu: "つ", te: "て", to: "と",
  da: "だ", di: "ぢ", du: "づ", de: "で", do: "ど",
  na: "な", ni: "に", nu: "ぬ", ne: "ね", no: "の",
  ha: "は", hi: "ひ", hu: "ふ", fu: "ふ", he: "へ", ho: "ほ",
  ba: "ば", bi: "び", bu: "ぶ", be: "べ", bo: "ぼ",
  pa: "ぱ", pi: "ぴ", pu: "ぷ", pe: "ぺ", po: "ぽ",
  ma: "ま", mi: "み", mu: "む", me: "め", mo: "も",
  ya: "や", yu: "ゆ", yo: "よ",
  ra: "ら", ri: "り", ru: "る", re: "れ", ro: "ろ",
  la: "ら", li: "り", lu: "る", le: "れ", lo: "ろ",
  wa: "わ", wi: "ゐ", we: "ゑ", wo: "を",
  fa: "ふぁ", fi: "ふぃ", fe: "ふぇ", fo: "ふぉ",
  xa: "ぁ", xi: "ぃ", xu: "ぅ", xe: "ぇ", xo: "ぉ",
  xya: "ゃ", xyu: "ゅ", xyo: "ょ", xtsu: "っ",
  a: "あ", i: "い", u: "う", e: "え", o: "お",
};

const CONSONANTS = new Set("bcdfghjklmpqrstvwxyz".split(""));
const VOWELS_OR_Y = new Set(["a", "i", "u", "e", "o", "y"]);

/** Convert ASCII romaji inside input string into hiragana (preserving existing kana). */
export function romajiToHiragana(raw) {
  const s = String(raw || "").toLowerCase();
  let out = "";
  let i = 0;

  while (i < s.length) {
    const ch = s[i];

    if (ch === "-") {
      out += "ー";
      i += 1;
      continue;
    }

    // Double consonant -> sokuon っ (except 'n', handled below)
    if (
      i + 1 < s.length &&
      ch === s[i + 1] &&
      CONSONANTS.has(ch) &&
      ch !== "n"
    ) {
      out += "っ";
      i += 1;
      continue;
    }
    if (s.startsWith("tch", i)) {
      out += "っ";
      i += 1;
      continue;
    }

    // Nasal n
    if (ch === "n") {
      const next = s[i + 1] || "";
      if (next === "n" || next === "'") {
        out += "ん";
        i += 2;
        continue;
      }
      if (next && !VOWELS_OR_Y.has(next)) {
        out += "ん";
        i += 1;
        continue;
      }
    }

    let matched = false;
    for (const len of [4, 3, 2, 1]) {
      const slice = s.slice(i, i + len);
      if (slice.length === len && ROMAJI_TABLE[slice]) {
        out += ROMAJI_TABLE[slice];
        i += len;
        matched = true;
        break;
      }
    }
    if (!matched) {
      out += ch;
      i += 1;
    }
  }
  return out;
}

/** Normalize user reading input (romaji, katakana, or hiragana) into trimmed hiragana. */
export function normalizeKanaInput(raw) {
  const hira = romajiToHiragana(String(raw || "").trim());
  let out = "";
  for (const ch of hira) {
    const code = ch.charCodeAt(0);
    if (code >= 0x30a1 && code <= 0x30f6) {
      out += String.fromCharCode(code - 0x60);
    } else if (!/\s/.test(ch)) {
      out += ch;
    }
  }
  // Trailing lone 'n' when submitting e.g. "kantan"
  if (out.endsWith("n")) {
    out = `${out.slice(0, -1)}ん`;
  }
  return out;
}
