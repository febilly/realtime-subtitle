(function(root){
const modules = {};
modules['./localized-text'] = (function(){ const exports = {}; const require = name => modules[name];
"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.LANGUAGE_NAMES = exports.CONTENT_LANGUAGES = void 0;
exports.contentLanguage = contentLanguage;
exports.parseLocalized = parseLocalized;
exports.parseFields = parseFields;
exports.localized = localized;
exports.localizedField = localizedField;
exports.fromLegacy = fromLegacy;
exports.validateFields = validateFields;
exports.CONTENT_LANGUAGES = ['zh', 'en', 'ja', 'ko', 'ru', 'es', 'pt'];
exports.LANGUAGE_NAMES = { zh: '中文', en: 'English', ja: '日本語', ko: '한국어', ru: 'Русский', es: 'Español', pt: 'Português' };
function contentLanguage(input) {
    const base = input.toLowerCase().split(/[-_,;]/)[0];
    return exports.CONTENT_LANGUAGES.includes(base) ? base : 'en';
}
function parseLocalized(value) {
    if (typeof value === 'string') {
        try {
            value = JSON.parse(value);
        }
        catch {
            return undefined;
        }
    }
    if (!value || typeof value !== 'object' || Array.isArray(value))
        return undefined;
    const obj = value;
    if (!obj.texts || typeof obj.texts !== 'object' || Array.isArray(obj.texts))
        return undefined;
    const texts = {};
    for (const lang of exports.CONTENT_LANGUAGES) {
        const text = obj.texts[lang];
        if (typeof text === 'string' && text.trim())
            texts[lang] = text.trim();
    }
    return { source: contentLanguage(String(obj.source || 'en')), texts };
}
function parseFields(value) {
    if (typeof value === 'string') {
        try {
            value = JSON.parse(value);
        }
        catch {
            return {};
        }
    }
    if (!value || typeof value !== 'object' || Array.isArray(value))
        return {};
    return Object.fromEntries(Object.entries(value).flatMap(([field, raw]) => { const text = parseLocalized(raw); return text ? [[field, text]] : []; }));
}
function localized(text, lang, fallback = '') {
    const value = parseLocalized(text);
    return value ? value.texts[contentLanguage(lang)] || value.texts.en || value.texts[value.source] || Object.values(value.texts)[0] || fallback : fallback;
}
function localizedField(row, field, lang) {
    return localized(parseFields(row.i18n)[field], lang, String(row[field] || ''));
}
function fromLegacy(value, fallback = '', source = 'zh') {
    return parseLocalized(value) || { source, texts: fallback.trim() ? { [source]: fallback.trim() } : {} };
}
/** Reject malformed writes instead of silently discarding languages or truncating content. */
function validateFields(raw, limits) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw))
        throw new Error('Invalid multilingual fields');
    const fields = {};
    for (const [field, value] of Object.entries(raw)) {
        if (!Object.prototype.hasOwnProperty.call(limits, field))
            throw new Error('Unknown multilingual field');
        const text = parseLocalized(value);
        if (!text || !value || typeof value !== 'object' || !exports.CONTENT_LANGUAGES.includes(value.source))
            throw new Error('Invalid multilingual text');
        for (const [lang, entry] of Object.entries(value.texts)) {
            if (!exports.CONTENT_LANGUAGES.includes(lang) || typeof entry !== 'string' || entry.length > limits[field] || entry.includes('\0'))
                throw new Error('Invalid multilingual translation');
        }
        // A missing source translation falls back deterministically, including after clearing it.
        text.source = text.texts[text.source] ? text.source : exports.CONTENT_LANGUAGES.find(lang => !!text.texts[lang]) || text.source;
        fields[field] = text;
    }
    return fields;
}

return exports; })();
modules['./content-templates'] = (function(){ const exports = {}; const require = name => modules[name];
"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.REASON_TEMPLATES = exports.PRODUCT_TEMPLATES = void 0;
exports.templateText = templateText;
const localized_text_1 = require("./localized-text");
const templates = {
    credits: ['{n} Credits', '{n} Credits', '{n} Credits', '{n} Credits', '{n} Credits', '{n} Credits', '{n} Credits'],
    monthly: ['包月套餐', 'Monthly plan', '月額プラン', '월간 요금제', 'Месячный план', 'Plan mensual', 'Plano mensal'],
    soniox_monthly: ['Soniox 包月', 'Soniox monthly', 'Soniox 月額', 'Soniox 월간', 'Soniox на месяц', 'Soniox mensual', 'Soniox mensal'],
    gemini_monthly: ['Gemini 包月', 'Gemini monthly', 'Gemini 月額', 'Gemini 월간', 'Gemini на месяц', 'Gemini mensual', 'Gemini mensal'],
    llm_monthly: ['LLM 翻译包月', 'LLM translation monthly', 'LLM 翻訳月額', 'LLM 번역 월간', 'Перевод LLM на месяц', 'Traducción LLM mensual', 'Tradução LLM mensal'],
    compensation: ['服务补偿', 'Service compensation', 'サービス補償', '서비스 보상', 'Компенсация за обслуживание', 'Compensación del servicio', 'Compensação pelo serviço'],
    promotion: ['活动奖励', 'Promotional reward', 'キャンペーン特典', '이벤트 보상', 'Награда по акции', 'Recompensa promocional', 'Recompensa promocional'],
    refund: ['退款', 'Refund', '返金', '환불', 'Возврат средств', 'Reembolso', 'Reembolso'],
    correction: ['人工修正', 'Manual correction', '手動修正', '수동 수정', 'Ручная корректировка', 'Corrección manual', 'Correção manual'],
};
exports.PRODUCT_TEMPLATES = ['credits', 'monthly', 'soniox_monthly', 'gemini_monthly', 'llm_monthly'];
exports.REASON_TEMPLATES = ['compensation', 'promotion', 'refund', 'correction'];
function templateText(id, amount = '') {
    return { source: 'zh', texts: Object.fromEntries(localized_text_1.CONTENT_LANGUAGES.map((lang, index) => [lang, templates[id][index].replace('{n}', amount)])) };
}

return exports; })();
modules['./server-messages'] = (function(){ const exports = {}; const require = name => modules[name];
"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.serverMessageKey = serverMessageKey;
exports.serverMessage = serverMessage;
exports.creditReason = creditReason;
exports.fixedText = fixedText;
const localized_text_1 = require("./localized-text");
const content_templates_1 = require("./content-templates");
// Stable identifiers and numeric parameters travel over the API; raw upstream details remain raw.
const messages = {
    authentication_required: ['请先登录', 'Please sign in', 'ログインしてください', '로그인해 주세요', 'Войдите в аккаунт', 'Inicia sesión', 'Entre na sua conta'],
    session_expired: ['登录已失效，请重新登录', 'Your login expired. Please sign in again.', 'ログインの有効期限が切れました。再ログインしてください。', '로그인이 만료되었습니다. 다시 로그인해 주세요.', 'Сеанс истёк. Войдите снова.', 'La sesión ha caducado. Vuelve a iniciar sesión.', 'A sessão expirou. Entre novamente.'],
    user_banned: ['账户已被封禁', 'This account is banned', 'このアカウントは利用停止中です', '이 계정은 이용이 정지되었습니다', 'Аккаунт заблокирован', 'Esta cuenta está suspendida', 'Esta conta está suspensa'],
    invalid_key: ['登录凭据无效或已撤销', 'Login credential is invalid or revoked', 'ログイン情報が無効または失効しています', '로그인 정보가 유효하지 않거나 해지되었습니다', 'Данные входа недействительны или отозваны', 'La credencial no es válida o ha sido revocada', 'A credencial é inválida ou foi revogada'],
    rate_limit: ['请求过于频繁，请稍后重试', 'Too many requests. Try again later.', 'リクエストが多すぎます。後で再試行してください。', '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.', 'Слишком много запросов. Повторите позже.', 'Demasiadas solicitudes. Inténtalo más tarde.', 'Muitas solicitações. Tente mais tarde.'],
    unavailable: ['服务暂时不可用，请稍后重试', 'Service temporarily unavailable. Try again later.', 'サービスを一時的に利用できません。後で再試行してください。', '서비스를 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.', 'Сервис временно недоступен. Повторите позже.', 'El servicio no está disponible temporalmente. Inténtalo más tarde.', 'Serviço temporariamente indisponível. Tente mais tarde.'],
    credits_exhausted: ['可用额度不足', 'Insufficient credits', '利用可能なクレジットが不足しています', '사용 가능한 크레딧이 부족합니다', 'Недостаточно кредитов', 'Créditos insuficientes', 'Créditos insuficientes'],
    concurrency_limit: ['已达到同时连接数量上限', 'Concurrent session limit reached', '同時接続数の上限に達しました', '동시 연결 한도에 도달했습니다', 'Достигнут лимит одновременных сеансов', 'Se ha alcanzado el límite de sesiones simultáneas', 'Limite de sessões simultâneas atingido'],
    model_not_allowed: ['服务器不允许使用此模型', 'This model is not allowed by the server', 'このモデルはサーバーで許可されていません', '서버에서 허용하지 않는 모델입니다', 'Сервер не разрешает эту модель', 'El servidor no permite este modelo', 'O servidor não permite este modelo'],
    invalid_code: ['兑换码无效、已使用或已过期', 'Code is invalid, used or expired', 'コードが無効、使用済み、または期限切れです', '코드가 유효하지 않거나 사용되었거나 만료되었습니다', 'Код недействителен, использован или просрочен', 'El código no es válido, ya se usó o ha caducado', 'O código é inválido, já foi usado ou expirou'],
    ticket_not_found: ['工单不存在', 'Ticket not found', 'お問い合わせが見つかりません', '문의 내역을 찾을 수 없습니다', 'Обращение не найдено', 'No se encontró el ticket', 'Chamado não encontrado'],
    invalid_content: ['多语言内容格式或长度不正确', 'Invalid multilingual content or length', '多言語コンテンツの形式または長さが正しくありません', '다국어 콘텐츠 형식 또는 길이가 올바르지 않습니다', 'Неверный формат или длина многоязычного текста', 'El formato o la longitud del contenido multilingüe no es válido', 'Formato ou tamanho inválido do conteúdo multilíngue'],
    translation_failed: ['翻译失败，请稍后重试', 'Translation failed. Please retry.', '翻訳に失敗しました。再試行してください。', '번역에 실패했습니다. 다시 시도해 주세요.', 'Ошибка перевода. Повторите попытку.', 'La traducción falló. Vuelve a intentarlo.', 'Falha na tradução. Tente novamente.'],
    stale_edit: ['内容已被其他操作修改，请刷新后重试', 'Content changed. Refresh and try again.', '内容が変更されました。更新して再試行してください。', '콘텐츠가 변경되었습니다. 새로 고침 후 다시 시도해 주세요.', 'Содержимое изменилось. Обновите и повторите.', 'El contenido ha cambiado. Actualiza e inténtalo de nuevo.', 'O conteúdo mudou. Atualize e tente novamente.'],
    admin_adjust: ['管理员手动调整', 'Administrator adjustment', '管理者による調整', '관리자 수동 조정', 'Корректировка администратором', 'Ajuste del administrador', 'Ajuste do administrador'],
    admin_set_balance: ['管理员手动设置余额', 'Administrator balance reset', '管理者による残高設定', '관리자 잔액 설정', 'Установка баланса администратором', 'Saldo establecido por el administrador', 'Saldo definido pelo administrador'],
    manual_user_create: ['管理员创建账户初始额度', 'Initial credits from administrator', '管理者による初期クレジット', '관리자 지급 초기 크레딧', 'Начальные кредиты от администратора', 'Créditos iniciales del administrador', 'Créditos iniciais do administrador'],
    registration_bonus: ['注册奖励', 'Registration bonus', '登録特典', '가입 보너스', 'Бонус за регистрацию', 'Bono de registro', 'Bônus de cadastro'],
    rank_upgrade_bonus: ['信任等级提升奖励', 'Trust rank upgrade bonus', '信頼ランク上昇特典', '신뢰 등급 상승 보너스', 'Бонус за повышение ранга доверия', 'Bono por subir el rango de confianza', 'Bônus por aumento do nível de confiança'],
    first_redeem_bonus: ['首充奖励', 'First top-up bonus', '初回チャージ特典', '첫 충전 보너스', 'Бонус за первое пополнение', 'Bono de primera recarga', 'Bônus da primeira recarga'],
    redeem_code: ['兑换码充值', 'Code redemption', 'コードによるチャージ', '코드 충전', 'Пополнение кодом', 'Recarga con código', 'Recarga com código'],
    invite_reward: ['邀请奖励', 'Invitation reward', '招待特典', '초대 보상', 'Награда за приглашение', 'Recompensa por invitación', 'Recompensa por convite'],
    invite_redemption_reward: ['邀请兑换返奖', 'Referral top-up reward', '招待チャージ還元', '초대 충전 보상', 'Награда за пополнение приглашённого', 'Recompensa por recarga de invitado', 'Recompensa por recarga de convidado'],
    relay_usage: ['字幕服务用量消费', 'Subtitle service usage', '字幕サービス利用', '자막 서비스 사용', 'Использование сервиса субтитров', 'Uso del servicio de subtítulos', 'Uso do serviço de legendas'],
};
const aliases = {
    'Authentication required': 'authentication_required', 'Login session expired or invalid': 'session_expired',
    'User is banned': 'user_banned', 'User banned': 'user_banned', 'Invalid API key': 'invalid_key', 'Invalid or revoked API key': 'invalid_key',
    'Rate limit exceeded': 'rate_limit', 'Too many requests': 'rate_limit',
    'Internal server error': 'unavailable', 'VRChat verification service unavailable': 'unavailable', 'No available upstream keys': 'unavailable', 'No direct relay backend is available': 'unavailable', 'No relay node is available for local sessions': 'unavailable', 'Relay admission failed': 'unavailable', 'Default translation model is unavailable': 'unavailable',
    'Insufficient credits': 'credits_exhausted', 'Billing exhausted': 'credits_exhausted',
    'Concurrency limit exceeded': 'concurrency_limit', 'Concurrent stream limit reached': 'concurrency_limit',
    'Code already used': 'invalid_code', 'Invalid code': 'invalid_code', 'Invalid or used code': 'invalid_code', 'Invalid redemption code': 'invalid_code', 'Code expired': 'invalid_code',
    'Ticket not found': 'ticket_not_found', 'Invalid multilingual content': 'invalid_content',
    'Content translation failed; retry later': 'translation_failed', 'Translation returned an invalid response': 'translation_failed',
    'Announcement changed or deleted; refresh and retry': 'stale_edit', 'Announcement list changed; refresh and retry': 'stale_edit',
};
function serverMessageKey(detail) {
    return (Object.prototype.hasOwnProperty.call(aliases, detail) ? aliases[detail] : undefined) || (/^Model '.+' is not allowed$/.test(detail) ? 'model_not_allowed' : undefined);
}
function serverMessage(detail, lang, key) {
    const id = key || serverMessageKey(detail) || '';
    const entries = Object.prototype.hasOwnProperty.call(messages, id) ? messages[id] : undefined;
    return entries?.[localized_text_1.CONTENT_LANGUAGES.indexOf((0, localized_text_1.contentLanguage)(lang))] || detail;
}
function creditReason(code, original, lang) {
    for (const id of content_templates_1.REASON_TEMPLATES)
        if (original === (0, content_templates_1.templateText)(id).texts.zh)
            return (0, localized_text_1.localized)((0, content_templates_1.templateText)(id), lang);
    // Preserve custom notes, even when they accompany a standard adjustment code.
    return messages[code]?.[0] === original ? serverMessage(original, lang, code) : original;
}
function fixedText(key, lang) { return messages[key]?.[localized_text_1.CONTENT_LANGUAGES.indexOf(lang)] || key; }

return exports; })();
root.ContentI18n = Object.assign({}, ...Object.values(modules));
})(typeof window !== 'undefined' ? window : globalThis);
