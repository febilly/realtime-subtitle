var koroman = (function (exports) {
  'use strict';

  // @name: koroman.core.js
  // @project: Koroman
  // @author: Donghe Youn (Daissue)
  // @date: 2025-04-02
  // @description: This module provides core functionality for romanizing Korean Hangul text.
  //               It includes functions to decompose Hangul syllables into their constituent jamo (initial, medial, final),
  //               apply Korean pronunciation rules, and convert them into Latin (Romanized) script.
  //               This module is used by both CommonJS and ESModule entry points and is not intended to be used directly.
  // @license: MIT License
  // @version: 1.0.16
  // @dependencies: None
  // @usage: Import this from koroman.mjs or koroman.cjs to access the romanize() function.
  // 자모 분해 및 조합 + 로마자 변환 (초성/중성/종성 실제 유니코드 문자 사용 버전)

  // 초성: 19자 (U+1100~U+1112)
  const CHO = [
      "ᄀ", "ᄁ", "ᄂ", "ᄃ", "ᄄ", "ᄅ", "ᄆ", "ᄇ", "ᄈ", "ᄉ",
      "ᄊ", "ᄋ", "ᄌ", "ᄍ", "ᄎ", "ᄏ", "ᄐ", "ᄑ", "ᄒ"
    ];

    // 중성: 21자 (U+1161~U+1175)
    const JUNG = [
      "ᅡ", "ᅢ", "ᅣ", "ᅤ", "ᅥ", "ᅦ", "ᅧ", "ᅨ", "ᅩ", "ᅪ", "ᅫ",
      "ᅬ", "ᅭ", "ᅮ", "ᅯ", "ᅰ", "ᅱ", "ᅲ", "ᅳ", "ᅴ", "ᅵ"
    ];

    // 종성: 28자 (첫 번째는 없음, 나머지 U+11A8~U+11C2)
    const JONG = [
      "", "ᆨ", "ᆩ", "ᆪ", "ᆫ", "ᆬ", "ᆭ", "ᆮ", "ᆯ", "ᆰ", "ᆱ", "ᆲ",
      "ᆳ", "ᆴ", "ᆵ", "ᆶ", "ᆷ", "ᆸ", "ᆹ", "ᆺ", "ᆻ", "ᆼ", "ᆽ", "ᆾ",
      "ᆿ", "ᇀ", "ᇁ", "ᇂ"
    ];

    const ZWSP = '\u200A'; // Hair Space (조합 방지용)
  //   const ZWSP = '\u200B'; // zero-width space (조합 방지용)

    // casingOption 별칭/숫자 매핑 (1.0.14~) — 풀네임/약어/숫자 모두 허용, case-insensitive
    const CASING_ALIASES = {
      'lowercase': 'lowercase', 'lower': 'lowercase', 'l': 'lowercase', 'lc': 'lowercase', '0': 'lowercase',
      'uppercase': 'uppercase', 'upper': 'uppercase', 'u': 'uppercase', 'uc': 'uppercase', '1': 'uppercase',
      'capitalize-line': 'capitalize-line', 'cap-line': 'capitalize-line', 'cline': 'capitalize-line', 'cl': 'capitalize-line', '2': 'capitalize-line',
      'capitalize-word': 'capitalize-word', 'cap-word': 'capitalize-word', 'cword': 'capitalize-word', 'cw': 'capitalize-word', '3': 'capitalize-word'
    };

    function normalizeCasingOption(opt) {
      if (opt === null || opt === undefined) return 'lowercase';
      const key = String(opt).toLowerCase();
      return CASING_ALIASES[key] ?? 'lowercase';
    }

    function formatRoman(str, casingOption = "lowercase") {
      switch (normalizeCasingOption(casingOption)) {
        case "uppercase": return str.toUpperCase();
        case "capitalize-line": return str.split('\n').map(line => line.length > 0 ? line.charAt(0).toUpperCase() + line.slice(1) : '').join('\n');
        case "capitalize-word": return str.split('\n').map(line => line.split(' ').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ')).join('\n');
        default: return str.toLowerCase();
      }
    }

    function splitHangulToJamos(str) {
      const result = [];
      let jamoString = '';
      let plainJamoString = '';
      for (let char of str) {
        const code = char.charCodeAt(0);
        if (code < 0xAC00 || code > 0xD7A3) {
          result.push({ char, type: 'non-hangul' });
          jamoString += char;
          plainJamoString += char;
          continue;
        }
        const syllableIndex = code - 0xAC00;
        const cho = Math.floor(syllableIndex / (21 * 28));
        const jung = Math.floor((syllableIndex % (21 * 28)) / 28);
        const jong = syllableIndex % 28;
        const jamo = { 초성: CHO[cho], 중성: JUNG[jung], 종성: JONG[jong] || null };
        result.push(jamo);
        jamoString += jamo.초성 + jamo.중성 + (jamo.종성 || '');
        plainJamoString += jamo.초성 + ZWSP + jamo.중성;
        if (jamo.종성) plainJamoString += ZWSP + jamo.종성;
      }
      return { jamoArray: result, jamoString, plainJamoString };
    }

    function composeJamos(jamoArray) {
      let result = '';
      for (let jamo of jamoArray) {
        if (jamo.type === 'non-hangul') {
          result += jamo.char;
          continue;
        }
        const choIndex = CHO.indexOf(jamo.초성);
        const jungIndex = JUNG.indexOf(jamo.중성);

const jongIndex = jamo.종성 ? JONG.indexOf(jamo.종성) : 0;
        if (choIndex < 0 || jungIndex < 0 || jongIndex < 0) {
          result += '?';
          continue;
        }
        const code = 0xAC00 + (choIndex * 21 + jungIndex) * 28 + jongIndex;
        result += String.fromCharCode(code);
      }
      return result;
    }

    // 표준발음법 29항 ㄴ첨가 — 형태소 분석 없이 적용 가능한 부분 집합.
    // 뒤 음절이 합성어에서 거의 고정인 트리거(잎)일 때만 적용. (많이·같이 등 단일어 오탐 방지)
    const N_INSERTION_TRIGGERS = new Set(['잎']);

    function hangulSyllableIndices(ch) {
      const n = ch.charCodeAt(0) - 0xAC00;
      return {
        cho: Math.floor(n / (21 * 28)),
        jung: Math.floor((n % (21 * 28)) / 28),
        jong: n % 28
      };
    }

    function composeHangulSyllable(cho, jung, jong = 0) {
      return String.fromCharCode(0xAC00 + (cho * 21 + jung) * 28 + jong);
    }

    /** 합성어 ㄴ첨가(29항) — 받침 + 잎 → 뒤 음절 초성 ㄴ 첨가 (꽃잎→[꼰닙] 등) */
    function applyNInsertionTriggers(text) {
      return text.replace(/[\uAC00-\uD7A3]+/g, (word) => {
        const chars = [...word];
        if (chars.length < 2) return word;
        const last = chars[chars.length - 1];
        if (!N_INSERTION_TRIGGERS.has(last)) return word;
        const prev = chars[chars.length - 2];
        const prevParts = hangulSyllableIndices(prev);
        if (prevParts.jong === 0) return word;
        const lastParts = hangulSyllableIndices(last);
        if (lastParts.cho !== 11) return word; // 잎 = ᄋ+ᅵ+…
        const nip = composeHangulSyllable(2, lastParts.jung, lastParts.jong); // ᄂ+중성+종성
        return chars.slice(0, -1).join('') + nip;
      });
    }

    function applyPronunciationRules(jamoStr) {
      const replaceArr = [

          // ==============================
          // 1. 무효화 처리
          // ==============================

          { p: /\u11a7/g, r: "" }, // 'ᆧ'(U+11A7) → 제거 (사용되지 않는 종성)

          // ==============================
          // 2. 비음화 (ㄴ, ㅁ, ㅇ)
          // ==============================

          { p: /[\u11b8\u11c1\u11b9\u11b2\u11b5](?=[\u1102\u1106])/g, r: "ᆷ" }, 
          // 종성 'ᆸ(ㅂ)' 'ᇁ(ㅍ)' 'ᆹ(ㅂㅅ)' 'ᆲ(ㄹㅂ)' 'ᆵ(ㄹㅍ)' + 다음 초성 'ᄂ(ㄴ)' or 'ᄆ(ㅁ)' → 'ᆷ'

          { p: /[\u11ae\u11c0\u11bd\u11be\u11ba\u11bb\u11c2](?=[\u1102\u1106])/g, r: "ᆫ" },
          // 종성 'ᆮ(ㄷ)' 'ᇀ(ㅌ)' 'ᆽ(ㅈ)' 'ᆾ(ㅊ)' 'ᆺ(ㅅ)' 'ᆻ(ㅆ)' 'ᇂ(ㅎ)' + 다음 초성 'ᄂ(ㄴ)' or 'ᄆ(ㅁ)' → 'ᆫ'

          { p: /[\u11a8\u11a9\u11bf\u11aa\u11b0](?=[\u1102\u1106])/g, r: "ᆼ" },
          // 종성 'ᆨ(ㄱ)' 'ᆩ(ㄲ)' 'ᆿ(ㅋ)' 'ᆪ(ㄱㅅ)' 'ᆰ(ㄹㄱ)' + 다음 초성 'ᄂ'/'ᄆ' → 'ᆼ'

          // ==============================
          // 3. 연음/연철
          // ==============================

          { p: /\u11a8\u110b(?=[\u1163\u1164\u1167\u1168\u116d\u1172])/g, r: "ᆼᄂ" },
          // 'ᆨ' + 'ᄋ' + 중성 'ㅑㅒㅕㅖㅛㅠ' → 'ᆼᄂ' (연음화)

          { p: /\u11af\u110b(?=[\u1163\u1164\u1167\u1168\u116d\u1172])/g, r: "ᆯᄅ" }, 
          // 'ᆯ' + 'ᄋ' + 중성 위와 같음 → 'ᆯᄅ'

          { p: /[\u11a8\u11bc]\u1105/g, r: "ᆼᄂ" }, 
          // 'ᆨ(ㄱ)', 'ᆼ(ㅇ)' + 'ᄅ(ㄹ)' → 'ᆼᄂ'

          { p: /\u11ab\u1105(?=\u1169)/g, r: "ᆫᄂ" }, 
          // 'ᆫ(ㄴ)' + 'ᄅ' + 중성 'ㅗ' → 'ᆫᄂ'

          { p: /\u11af\u1102|\u11ab\u1105/g, r: "ᆯᄅ" }, 
          // 'ᆯ(ㄹ)' + 'ᄂ(ㄴ)', 'ᆫ(ㄴ)' + 'ᄅ(ㄹ)' → 'ᆯᄅ'

          { p: /[\u11b7\u11b8]\u1105/g, r: "ᆷᄂ" }, 
          // 'ᆷ(ㅁ)', 'ᆸ(ㅂ)' + 'ᄅ' → 'ᆷᄂ'

          { p: /\u11b0\u1105/g, r: "ᆨᄅ" }, 
          // 'ᆰ(ㄹㄱ)' + 'ᄅ' → 'ᆨᄅ'

          // ==============================
          // 4. 격음화 / 자음군 분해
          // ==============================

          { p: /\u11a8\u110f/g, r: "ᆨ-ᄏ" }, // 'ᆨ' + 'ᄏ' → 'ᆨ-ᄏ'
          { p: /\u11b8\u1111/g, r: "ᆸ-ᄑ" }, // 'ᆸ' + 'ᄑ' → 'ᆸ-ᄑ'
          { p: /\u11ae\u1110/g, r: "ᆮ-ᄐ" }, // 'ᆮ' + 'ᄐ' → 'ᆮ-ᄐ'

          // ==============================
          // 5. 복합 종성(자음군) + 'ᄋ' 모음 연음 처리 (표준발음법 14항)

//    뒤 자음만 다음 음절 초성으로 옮겨감. 분해보다 먼저 적용해야
          //    이중종성 dedup 규칙으로 자음이 잘못 사라지지 않는다.
          //    예: 닭이[달기] → ᆰᄋ → ᆯᄀ → "dalgi"
          //        잃어[이러] → ᆶᄋ → ᆯᄋ → 후속 연음으로 ᄅ → "ireo"
          //    (된소리는 표기에 반영하지 않음 — 넋이[넉씨] → "neoksi")
          // ==============================

          { p: /\u11aa\u110b/g, r: "\u11a8\u1109" }, // ᆪ + 'ᄋ' → ᆨ + 'ᄉ' (넋이 → 넉시)
          { p: /\u11ac\u110b/g, r: "\u11ab\u110c" }, // ᆬ + 'ᄋ' → ᆫ + 'ᄌ' (앉아 → 안자)
          { p: /\u11ad\u110b/g, r: "\u11ab\u110b" }, // ᆭ + 'ᄋ' → ᆫ + 'ᄋ' (ㅎ만 탈락; 일관성)
          { p: /\u11b0\u110b/g, r: "\u11af\u1100" }, // ᆰ + 'ᄋ' → ᆯ + 'ᄀ' (닭이 → 달기)
          { p: /\u11b1\u110b/g, r: "\u11af\u1106" }, // ᆱ + 'ᄋ' → ᆯ + 'ᄆ' (삶이 → 살미)
          { p: /\u11b2\u110b/g, r: "\u11af\u1107" }, // ᆲ + 'ᄋ' → ᆯ + 'ᄇ' (밟아 → 발바)
          { p: /\u11b3\u110b/g, r: "\u11af\u1109" }, // ᆳ + 'ᄋ' → ᆯ + 'ᄉ' (곬이 → 골시)
          { p: /\u11b4\u110b/g, r: "\u11af\u1110" }, // ᆴ + 'ᄋ' → ᆯ + 'ᄐ' (핥아 → 할타)
          { p: /\u11b5\u110b/g, r: "\u11af\u1111" }, // ᆵ + 'ᄋ' → ᆯ + 'ᄑ' (읊어 → 을퍼)
          { p: /\u11b6\u110b/g, r: "\u11af\u110b" }, // ᆶ + 'ᄋ' → ᆯ + 'ᄋ' (잃어 → 이러; ㅎ만 탈락)
          { p: /\u11b9\u110b/g, r: "\u11b8\u1109" }, // ᆹ + 'ᄋ' → ᆸ + 'ᄉ' (값이 → 갑시)

          // ==============================
          // 5b. 밟- 어간 예외 (표준발음법 제10항 예외)
          //    'ㄼ' 받침은 일반적으로 [ㄹ]로 발음되지만, '밟-' 어간은 자음 앞에서 [ㅂ]로 발음.
          //    예: 밟다[밥따] → bapda, 밟지[밥찌] → bapji, 밟고[밥꼬] → bapgo, 밟는[밤는] → bamneun (비음화 우선)
          //    위 5번 규칙으로 'ᆲ+ᄋ' (밟아) 케이스는 이미 처리됨.
          // ==============================

          { p: /\u1107\u1161\u11b2(?=[\u1100-\u110A\u110C-\u1112])/g, r: "\u1107\u1161\u11b8" },

          // ==============================
          // 6. 복합 종성 분해 (자음군 단순화)
          //    분해 순서: 받침 위치에서 [표준 발음법상 남는 자음]을 첫 자모로 둠.
          //    뒤이은 이중종성 dedup 규칙으로 두 번째 자모가 떨어져 나가도록 함.
          //    (예: 흙[흑] → ᆨᆯ → ᆨ, 삶[삼] → ᆷᆯ → ᆷ, 읊[읍] → ᇁᆯ → ᇁ)
          // ==============================

          { p: /\u11aa/g, r: "ᆨᆺ" }, // 'ᆪ(ㄱㅅ)' → [ㄱ] 우세
          { p: /\u11ac/g, r: "ᆫᆽ" }, // 'ᆬ(ㄴㅈ)' → [ㄴ] 우세
          { p: /\u11ad/g, r: "ᆫᇂ" }, // 'ᆭ(ㄴㅎ)' → [ㄴ] 우세
          { p: /\u11b0/g, r: "ᆨᆯ" }, // 'ᆰ(ㄹㄱ)' → [ㄱ] 우세 (예: 흙→heuk)
          { p: /\u11b1/g, r: "ᆷᆯ" }, // 'ᆱ(ㄹㅁ)' → [ㅁ] 우세 (예: 삶→sam)
          { p: /\u11b2/g, r: "ᆯᆸ" }, // 'ᆲ(ㄹㅂ)' → [ㄹ] 우세 (밟- 예외 [ㅂ]는 형태소 정보 필요)
          { p: /\u11b3/g, r: "ᆯᆺ" }, // 'ᆳ(ㄹㅅ)' → [ㄹ] 우세
          { p: /\u11b4/g, r: "ᆯᇀ" }, // 'ᆴ(ㄹㅌ)' → [ㄹ] 우세
          { p: /\u11b5/g, r: "ᇁᆯ" }, // 'ᆵ(ㄹㅍ)' → [ㅂ] 우세 (예: 읊→eup)
          { p: /\u11b6/g, r: "ᆯᇂ" }, // 'ᆶ(ㄹㅎ)' → [ㄹ] 우세 (모음 앞은 위 5단계에서 선처리)
          { p: /\u11b9/g, r: "ᆸᆺ" }, // 'ᆹ(ㅂㅅ)' → [ㅂ] 우세

          // ==============================
          // 7. 구개음화 (ㄷ/ㅌ + ᄋㅣ → 지/치) + ㄷ+ㅎ+ㅣ → 치 (격음화+구개음화 통합)
          // ==============================

          { p: /\u11ae\u110b\u1175/g, r: "지" }, // 'ᆮ' + 'ᄋ' + 'ᅵ' → '지'
          { p: /\u11c0\u110b\u1175/g, r: "치" }, // 'ᇀ' + 'ᄋ' + 'ᅵ' → '치'


          // ==============================
          // 7b. ㄷ + ㅎ + ㅣ → 치 (받침 ㄷ + 초성 ㅎ + 중성 ㅣ: 격음화 결과인 ㅌ가 다시 구개음화)
          //     예: 굳히다 → 구치다, 닫히다 → 다치다
          { p: /\u11ae\u1112\u1175/g, r: "\u110e\u1175" },

          // ==============================

// 8. 연음 / 이음자 제거 (받침 + ᄋ)
          // ==============================

          { p: /\u11a8\u110b/g, r: "ᄀ" }, // 'ᆨ' + 'ᄋ' → 'ᄀ'
          { p: /\u11a9\u110b/g, r: "ᄁ" }, // 'ᆩ' + 'ᄋ' → 'ᄁ'
          { p: /\u11ae\u110b/g, r: "ᄃ" }, // 'ᆮ' + 'ᄋ' → 'ᄃ'
          { p: /\u11af\u110b/g, r: "ᄅ" }, // 'ᆯ' + 'ᄋ' → 'ᄅ'
          { p: /\u11b8\u110b/g, r: "ᄇ" }, // 'ᆸ' + 'ᄋ' → 'ᄇ'
          { p: /\u11ba\u110b/g, r: "ᄉ" }, // 'ᆺ' + 'ᄋ' → 'ᄉ'
          { p: /\u11bb\u110b/g, r: "ᄊ" }, // 'ᆻ' + 'ᄋ' → 'ᄊ'
          { p: /\u11bd\u110b/g, r: "ᄌ" }, // 'ᆽ' + 'ᄋ' → 'ᄌ'
          { p: /\u11be\u110b/g, r: "ᄎ" }, // 'ᆾ' + 'ᄋ' → 'ᄎ'
          { p: /\u11c2\u110b/g, r: "" },  // 'ᇂ' + 'ᄋ' → 제거

          // ==============================
          // 9. 거센소리되기 (Aspiration) — ROMANIZE_RULE 4 기본 정책
          //    체언 예외(묵호→Mukho, 집현전→Jiphyeonjeon)는 형태소 분석 없이는 분리 불가하므로
          //    일관되게 단일 격음(k/t/p/ch)으로 처리. 표준 발음 결과 기준.
          // ==============================

          { p: /\u11c2\u1100|\u11a8\u1112/g, r: "ᄏ" }, // 'ᇂ'+'ᄀ' or 'ᆨ'+'ᄒ' → 'ᄏ' (좋고→joko)
          { p: /\u11c2\u1103|\u11ae\u1112/g, r: "ᄐ" }, // 'ᇂ'+'ᄃ' or 'ᆮ'+'ᄒ' → 'ᄐ' (놓다→nota)
          { p: /\u11c2\u110c|\u11bd\u1112/g, r: "ᄎ" }, // 'ᇂ'+'ᄌ' or 'ᆽ'+'ᄒ' → 'ᄎ' (낳지→nachi, 맞히다→machida)
          { p: /\u11c2\u1107/g, r: "ᄇ" },              // 'ᇂ'+'ᄇ' → 'ᄇ'
          { p: /\u11b8\u1112/g, r: "ᄑ" },              // 'ᆸ'+'ᄒ' → 'ᄑ' (잡혀→japyeo)

          // ==============================
          // 10. 특수 처리 및 최종 정리
          // ==============================

          { p: /\u11af\u1105/g, r: "ll" }, // 'ᆯ' + 'ᄅ' → ll
          { p: /\u11c2(?!\s|$)/g, r: "" }, // 'ᇂ' (종성) 단독 → 제거
          { p: /([\u11a8-\u11c2])([\u11a8-\u11c2])/g, r: "$1" } // 이중 종성 제거 (자음군 단순화 마무리)
        ];

      for (const { p, r } of replaceArr) {
        jamoStr = jamoStr.replace(p, r);
      }
      return jamoStr;
    }

    // 자모 → 로마자 매핑 테이블
    //
    // ROMAN_MAP_STD: 표준 발음 기반 매핑 (ROMANIZE_RULE 3·4). usePronunciationRules=true 일 때 사용.
    //   - 받침 ㄷ/ㅈ/ㅊ/ㅌ/ㅎ → 'ㄷ' 대표음(t), 받침 ㅎ도 단독으로 남으면 't'.
    //
    // ROMAN_MAP_LITERAL: 직역/학술적 표기에 가까운 매핑 (ROMANIZE_RULE 5 일부 반영).
    //   usePronunciationRules=false 일 때 사용. 음운변화 가정 없이 글자를 그대로 옮김.
    //   - 종성 ㄷ → 'd' (예: 굳이 → gudi), 종성 ㅎ → 'h' (예: 좋다 → johda)
    //   - 기존 1.0.14 동작과 호환.
    const ROMAN_MAP_STD = {
      'ᄀ': 'g', 'ᄁ': 'kk', 'ᄂ': 'n', 'ᄃ': 'd', 'ᄄ': 'tt',
      'ᄅ': 'r', 'ᄆ': 'm', 'ᄇ': 'b', 'ᄈ': 'pp', 'ᄉ': 's', 'ᄊ': 'ss',
      'ᄋ': '', 'ᄌ': 'j', 'ᄍ': 'jj', 'ᄎ': 'ch', 'ᄏ': 'k',
      'ᄐ': 't', 'ᄑ': 'p', 'ᄒ': 'h',

      'ᅡ': 'a', 'ᅢ': 'ae', 'ᅣ': 'ya', 'ᅤ': 'yae', 'ᅥ': 'eo', 'ᅦ': 'e',
      'ᅧ': 'yeo', 'ᅨ': 'ye', 'ᅩ': 'o', 'ᅪ': 'wa', 'ᅫ': 'wae',
      'ᅬ': 'oe', 'ᅭ': 'yo', 'ᅮ': 'u', 'ᅯ': 'wo', 'ᅰ': 'we',
      'ᅱ': 'wi', 'ᅲ': 'yu', 'ᅳ': 'eu', 'ᅴ': 'ui', 'ᅵ': 'i',

      'ᆨ': 'k', 'ᆩ': 'k', 'ᆪ': 'k', 'ᆫ': 'n', 'ᆬ': 'n', 'ᆭ': 'n', 'ᆮ': 't',
      'ᆯ': 'l', 'ᆰ': 'k', 'ᆱ': 'm', 'ᆲ': 'p', 'ᆳ': 't', 'ᆴ': 't', 'ᆵ': 'p', 'ᆶ': 'l',
      'ᆷ': 'm', 'ᆸ': 'p', 'ᆹ': 'p', 'ᆺ': 't', 'ᆻ': 't', 'ᆼ': 'ng',
      'ᆽ': 't', 'ᆾ': 't', 'ᆿ': 'k', 'ᇀ': 't', 'ᇁ': 'p', 'ᇂ': 't'
    };

    const ROMAN_MAP_LITERAL = {
      ...ROMAN_MAP_STD,
      'ᆮ': 'd',
      'ᇂ': 'h'
    };

    function applyRomanMapping(jamoStr, map = ROMAN_MAP_STD) {
      return [...jamoStr].map(ch => map[ch] ?? ch).join('');
    }

    // ---- 음절 재합성 + 붙임표(useHyphen) 처리 ------------------------------------
    // 음운규칙이 적용된 자모열을 다시 (초/중/종) 음절 단위로 묶어
    // 음절 경계 모호 케이스에 '-'를 삽입한다.
    //
    // 모호 케이스 (표준 로마자 표기법: "발음상 혼동의 우려가 있을 때 붙임표"):

//   1. 앞 음절 종성 ᆼ(ng) + 뒤 음절 초성 ᄋ(모음시작)        예) 중앙 → jung-ang
    //   2. 앞 음절 종성 ᆫ(n)  + 뒤 음절 초성 ᄀ(g)               예) 반구대 → ban-gudae
    //   3. 앞 음절 종성 없음   + 뒤 음절 초성 ᄋ(모음시작)         예) 해운대 → hae-undae, 세운 → se-un
    // ---------------------------------------------------------------------------

    const CHO_RANGE = [0x1100, 0x1112];
    const JUNG_RANGE = [0x1161, 0x1175];
    const JONG_RANGE = [0x11A8, 0x11C2];

    function inRange(code, [lo, hi]) { return code >= lo && code <= hi; }

    // 자모열을 [{type:'syllable', cho, jung, jong}, {type:'other', char}] 시퀀스로 그룹화.
    // 종성 없는 음절은 jong === ''. 초성/중성 매칭 실패한 단독 자모/문자는 'other'.
    function regroupJamosToSyllables(jamoStr) {
      const groups = [];
      let i = 0;
      while (i < jamoStr.length) {
        const ch = jamoStr[i];
        const code = ch.charCodeAt(0);
        if (inRange(code, CHO_RANGE) && i + 1 < jamoStr.length
            && inRange(jamoStr.charCodeAt(i + 1), JUNG_RANGE)) {
          const cho = ch;
          const jung = jamoStr[i + 1];
          let jong = '';
          let step = 2;
          if (i + 2 < jamoStr.length && inRange(jamoStr.charCodeAt(i + 2), JONG_RANGE)) {
            jong = jamoStr[i + 2];
            step = 3;
          }
          groups.push({ type: 'syllable', cho, jung, jong });
          i += step;
          continue;
        }
        groups.push({ type: 'other', char: ch });
        i++;
      }
      return groups;
    }

    function romanizeGroup(g, map) {
      if (g.type === 'syllable') {
        return (map[g.cho] ?? '') + (map[g.jung] ?? '') + (g.jong ? (map[g.jong] ?? '') : '');
      }
      return map[g.char] ?? g.char;
    }

    function needsHyphen(prev, cur) {
      if (!prev || !cur || prev.type !== 'syllable' || cur.type !== 'syllable') return false;
      const prevJong = prev.jong;
      const curCho = cur.cho;
      // 규칙1: ᆼ + ᄋ (모음 시작)
      if (prevJong === '\u11BC' && curCho === '\u110B') return true;
      // 규칙2: ᆫ + ᄀ
      if (prevJong === '\u11AB' && curCho === '\u1100') return true;
      // 규칙3: 받침 없음 + ᄋ (모음 시작)
      if (!prevJong && curCho === '\u110B') return true;
      return false;
    }

    function applyRomanMappingWithHyphen(jamoStr, map = ROMAN_MAP_STD) {
      const groups = regroupJamosToSyllables(jamoStr);
      let out = '';
      for (let i = 0; i < groups.length; i++) {
        const cur = groups[i];
        if (i > 0 && needsHyphen(groups[i - 1], cur)) {
          // 이미 끝이 '-'(예: 'ᆨ-ᄏ' 규칙에 의해 삽입)이면 중복 삽입 방지
          if (!out.endsWith('-')) out += '-';
        }
        out += romanizeGroup(cur, map);
      }
      return out;
    }

    // ---- 사용자 사전(customDictionary) 처리 -------------------------------------
    // 사용자가 사전을 넘기면 해당 한국어 단어를 사용자가 지정한 로마자 표기로 최우선 변환한다.
    // 사전 값은 casing 옵션의 영향을 받지 않으며(사용자 의도 보존), 음운규칙/매핑도 거치지 않는다.
    //
    // 동작 방식:
    //  1) 한국어 텍스트에서 사전 키를 길이 내림차순으로 찾아 placeholder(\u0001N\u0001)로 치환
    //  2) 평소대로 romanize 파이프라인 수행 (placeholder는 비한글이라 그대로 보존됨)
    //  3) casing 후처리까지 끝낸 결과에서 placeholder를 사전 값으로 복원
    // ---------------------------------------------------------------------------
    const DICT_MARK = '\u0001';
    const DICT_PATTERN = /\u0001(\d+)\u0001/g;

    // 모듈 내부 영구 사전 저장소. set/add/remove/clear 함수로 관리하고,
    // romanize 호출 시 useCustomDictionary 옵션(기본 true)에 따라 자동 사용됨.
    const _customDictionaryStore = {};

    function _isPlainObject(v) {
      return v !== null && typeof v === 'object' && !Array.isArray(v);
    }

    function _resetStore() {
      for (const k of Object.keys(_customDictionaryStore)) delete _customDictionaryStore[k];
    }

    function _mergeIntoStore(dict) {
      if (!_isPlainObject(dict)) return;
      for (const [k, v] of Object.entries(dict)) {
        if (k && typeof k === 'string') _customDictionaryStore[k] = String(v);
      }
    }

/** 내부 사전을 통째로 교체한다. 빈 객체/falsy 값을 넘기면 전체 비움. */
    function setCustomDictionary(dict) {
      _resetStore();
      _mergeIntoStore(dict);
    }

    /**
     * 내부 사전에 항목을 추가/병합한다.
     *  - addCustomDictionary({ "김철수": "Kim Chul-soo" })
     *  - addCustomDictionary("김철수", "Kim Chul-soo")
     */
    function addCustomDictionary(dictOrKey, value) {
      if (typeof dictOrKey === 'string') {
        if (dictOrKey) _customDictionaryStore[dictOrKey] = String(value);
      } else if (_isPlainObject(dictOrKey)) {
        _mergeIntoStore(dictOrKey);
      }
    }

    /** 내부 사전에서 특정 키를 제거한다. 제거되었으면 true. */
    function removeCustomDictionaryEntry(key) {
      if (typeof key === 'string' && key in _customDictionaryStore) {
        delete _customDictionaryStore[key];
        return true;
      }
      return false;
    }

    /** 내부 사전을 모두 비운다. */
    function clearCustomDictionary() {
      _resetStore();
    }

    /** 현재 내부 사전의 스냅샷(얕은 복사)을 반환한다 (리스팅). */
    function getCustomDictionary() {
      return { ..._customDictionaryStore };
    }

    function applyCustomDictionary(text, dict) {
      if (!dict) return { text, values: [] };
      const keys = Object.keys(dict).filter(k => k && k.length > 0).sort((a, b) => b.length - a.length);
      if (keys.length === 0) return { text, values: [] };
      const values = [];
      for (const key of keys) {
        const value = dict[key];
        let pos = 0;
        while (true) {
          const idx = text.indexOf(key, pos);
          if (idx === -1) break;
          const placeholder = `${DICT_MARK}${values.length}${DICT_MARK}`;
          values.push(value);
          text = text.slice(0, idx) + placeholder + text.slice(idx + key.length);
          pos = idx + placeholder.length;
        }
      }
      return { text, values };
    }

    function restoreCustomDictionary(text, values) {
      if (!values || values.length === 0) return text;
      return text.replace(DICT_PATTERN, (_, n) => {
        const i = parseInt(n, 10);
        return (i >= 0 && i < values.length) ? values[i] : '';
      });
    }

    // 내부 사전 + 옵션 사전을 병합해 실효 사전을 만든다.
    // - useCustomDictionary=false: 내부 사전 무시
    // - customDictionary 옵션: 항상 병합되며 동일 키는 옵션 사전이 우선
    function _resolveEffectiveDictionary(useCustomDictionary, customDictionary) {
      const internal = useCustomDictionary ? _customDictionaryStore : null;
      if (!internal && !_isPlainObject(customDictionary)) return null;
      const out = internal ? { ...internal } : {};
      if (_isPlainObject(customDictionary)) Object.assign(out, customDictionary);
      return out;
    }

    function romanize(str, {
      usePronunciationRules = true,
      casingOption = "lowercase",
      useHyphen = false,
      useCustomDictionary = true,
      customDictionary
    } = {}) {
      const effectiveDict = _resolveEffectiveDictionary(useCustomDictionary, customDictionary);
      const { text: prepped, values } = applyCustomDictionary(str, effectiveDict);
      const textForJamo = usePronunciationRules ? applyNInsertionTriggers(prepped) : prepped;
      const { jamoString } = splitHangulToJamos(textForJamo);
      const replaced = usePronunciationRules ? applyPronunciationRules(jamoString) : jamoString;
      const map = usePronunciationRules ? ROMAN_MAP_STD : ROMAN_MAP_LITERAL;
      const romanized = useHyphen ? applyRomanMappingWithHyphen(replaced, map) : applyRomanMapping(replaced, map);
      const cased = formatRoman(romanized, casingOption);
      return restoreCustomDictionary(cased, values);
    }

  exports.addCustomDictionary = addCustomDictionary;
  exports.applyCustomDictionary = applyCustomDictionary;
  exports.applyPronunciationRules = applyPronunciationRules;
  exports.applyRomanMapping = applyRomanMapping;
  exports.applyRomanMappingWithHyphen = applyRomanMappingWithHyphen;
  exports.clearCustomDictionary = clearCustomDictionary;
  exports.composeJamos = composeJamos;
  exports.formatRoman = formatRoman;
  exports.getCustomDictionary = getCustomDictionary;
  exports.normalizeCasingOption = normalizeCasingOption;
  exports.removeCustomDictionaryEntry = removeCustomDictionaryEntry;
  exports.restoreCustomDictionary = restoreCustomDictionary;

exports.romanize = romanize;
  exports.setCustomDictionary = setCustomDictionary;
  exports.splitHangulToJamos = splitHangulToJamos;

  return exports;

})({});

