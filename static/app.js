let ws;
const subtitleContainer = document.getElementById('subtitleContainer');
const themeToggle = document.getElementById('themeToggle');
const themeIcon = document.getElementById('themeIcon');
const restartButton = document.getElementById('restartButton');
const pauseButton = document.getElementById('pauseButton');
const pauseIcon = document.getElementById('pauseIcon');
const audioSourceButton = document.getElementById('audioSourceButton');
const audioSourceIcon = document.getElementById('audioSourceIcon');
const segmentModeButton = document.getElementById('segmentModeButton');
const segmentModeText = document.getElementById('segmentModeText');
const displayModeButton = document.getElementById('displayModeButton');
const displayModeText = document.getElementById('displayModeText');
const oscTranslationButton = document.getElementById('oscTranslationButton');
const oscTranslationIcon = document.getElementById('oscTranslationIcon');
const furiganaButton = document.getElementById('furiganaButton');
const furiganaIcon = document.getElementById('furiganaIcon');

// 存储所有已确认的tokens
let allFinalTokens = [];
// 存储当前未确认的tokens
let currentNonFinalTokens = [];
// 记录已合并到的位置（allFinalTokens 中的索引）
let lastMergedIndex = 0;

// 缓存已渲染的句子 HTML（用于增量渲染，键为 sentenceId）
let renderedSentences = new Map();
// 缓存已渲染的 speaker/块 HTML（用于按块增量渲染，键为 blockId）
let renderedBlocks = new Map();

const SCROLL_STICKY_THRESHOLD = 50;
let autoStickToBottom = true;
let tokenSequenceCounter = 0;

// 分段模式: 'translation' 或 'endpoint'（默认按 <end> 分段）
let segmentMode = localStorage.getItem('segmentMode') || 'endpoint';

// 显示模式: 'both', 'original', 'translation'
let displayMode = localStorage.getItem('displayMode') || 'both';

// OSC 翻译发送开关（默认关闭）
let oscTranslationEnabled = false;

// 日语假名注音开关（默认关闭）
let furiganaEnabled = localStorage.getItem('furiganaEnabled') === 'true';
// 假名注音缓存（避免重复请求）
let furiganaCache = new Map();
const pendingFuriganaRequests = new Set();

// 控制标志
let shouldReconnect = true;  // 是否应该自动重连
let isRestarting = false;    // 是否正在重启中
let isPaused = false;        // 是否暂停中
let audioSource = 'system';  // 音频输入来源

// 初始化按钮文本
updateSegmentModeButton();
updateDisplayModeButton();
updateAudioSourceButton();
updateFuriganaButton();
updateOscTranslationButton();

// 主题切换功能（默认深色）
let isDarkTheme = true;
document.body.classList.add('dark-theme');
themeIcon.textContent = '🌙';

// 从localStorage加载主题偏好，覆盖默认值
const savedTheme = localStorage.getItem('theme');
if (savedTheme === 'light') {
    isDarkTheme = false;
    document.body.classList.remove('dark-theme');
    themeIcon.textContent = '☀️';
}

themeToggle.addEventListener('click', () => {
    isDarkTheme = !isDarkTheme;
    
    if (isDarkTheme) {
        document.body.classList.add('dark-theme');
        themeIcon.textContent = '🌙';
        localStorage.setItem('theme', 'dark');
    } else {
        document.body.classList.remove('dark-theme');
        themeIcon.textContent = '☀️';
        localStorage.setItem('theme', 'light');
    }
});

// 更新分段模式按钮文本
function updateSegmentModeButton() {
    if (!segmentModeButton) {
        return;
    }

    if (segmentMode === 'translation') {
        segmentModeButton.title = 'Segment by translation (click to switch to endpoint mode)';
    } else {
        segmentModeButton.title = 'Segment by endpoint (click to switch to translation mode)';
    }
}

// 更新显示模式按钮文本
function updateDisplayModeButton() {
    if (displayMode === 'both') {
        displayModeButton.title = 'Show both original and translation';
    } else if (displayMode === 'original') {
        displayModeButton.title = 'Show original only';
    } else {
        displayModeButton.title = 'Show translation only';
    }
}

function updateOscTranslationButton() {
    if (!oscTranslationButton || !oscTranslationIcon) {
        return;
    }

    if (oscTranslationEnabled) {
        oscTranslationButton.classList.add('active');
        oscTranslationButton.title = 'Sending translation to VRChat (click to disable)';
    } else {
        oscTranslationButton.classList.remove('active');
        oscTranslationButton.title = 'Send translation to VRChat via OSC';
    }
}

function updateAudioSourceButton() {
    if (!audioSourceButton || !audioSourceIcon) {
        return;
    }

    if (audioSource === 'microphone') {
        audioSourceIcon.textContent = '🎤';
        audioSourceButton.title = 'Switch to system audio capture';
    } else {
        audioSourceIcon.textContent = '🔊';
        audioSourceButton.title = 'Switch to microphone capture';
    }
}

async function fetchInitialAudioSource() {
    try {
        const stored = localStorage.getItem('audioSource');
        if (stored === 'system' || stored === 'microphone') {
            audioSource = stored;
            updateAudioSourceButton();
        }
    } catch (storageError) {
        console.warn('Unable to access stored audio source preference:', storageError);
    }

    try {
        const response = await fetch('/audio-source');
        if (!response.ok) {
            return;
        }

        const data = await response.json();
        if (data && (data.source === 'system' || data.source === 'microphone')) {
            audioSource = data.source;
            updateAudioSourceButton();
            try {
                localStorage.setItem('audioSource', audioSource);
            } catch (persistError) {
                console.warn('Unable to persist audio source preference:', persistError);
            }
        }
    } catch (error) {
        console.error('Failed to fetch current audio source:', error);
    }
}

// 分段模式切换
segmentModeButton.addEventListener('click', () => {
    segmentMode = segmentMode === 'translation' ? 'endpoint' : 'translation';
    localStorage.setItem('segmentMode', segmentMode);
    updateSegmentModeButton();
    renderSubtitles();
    console.log(`Segmentation mode switched to: ${segmentMode}`);
});

// 显示模式切换
displayModeButton.addEventListener('click', () => {
    if (displayMode === 'both') {
        displayMode = 'original';
    } else if (displayMode === 'original') {
        displayMode = 'translation';
    } else {
        displayMode = 'both';
    }
    localStorage.setItem('displayMode', displayMode);
    updateDisplayModeButton();
    renderSubtitles();  // 立即重新渲染
    console.log(`Display mode switched to: ${displayMode}`);
});

if (oscTranslationButton) {
    oscTranslationButton.addEventListener('click', async () => {
        const next = !oscTranslationEnabled;
        try {
            const response = await fetch('/osc-translation', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: next })
            });

            let data = null;
            try {
                data = await response.json();
            } catch (parseError) {
                console.error('Failed to parse OSC translation toggle response:', parseError);
            }

            if (response.ok && data) {
                oscTranslationEnabled = !!data.enabled;
                updateOscTranslationButton();
                console.log(`OSC translation ${oscTranslationEnabled ? 'enabled' : 'disabled'}`);
            } else {
                console.error('Failed to toggle OSC translation:', response.status, data?.message);
            }
        } catch (error) {
            console.error('Error toggling OSC translation:', error);
        }
    });
}

// 假名注音开关
function updateFuriganaButton() {
    if (!furiganaButton || !furiganaIcon) {
        return;
    }
    
    if (furiganaEnabled) {
        furiganaButton.classList.add('active');
        furiganaButton.title = 'Furigana enabled (click to disable)';
    } else {
        furiganaButton.classList.remove('active');
        furiganaButton.title = 'Furigana disabled (click to enable)';
    }
}

if (furiganaButton) {
    furiganaButton.addEventListener('click', () => {
        furiganaEnabled = !furiganaEnabled;
        localStorage.setItem('furiganaEnabled', furiganaEnabled);
        updateFuriganaButton();
        // 清空缓存以便重新渲染
        furiganaCache.clear();
        pendingFuriganaRequests.clear();
        renderedSentences.clear();
        renderSubtitles();
        console.log(`Furigana ${furiganaEnabled ? 'enabled' : 'disabled'}`);
    });
}

// 重启识别功能
restartButton.addEventListener('click', async () => {
    if (restartButton.classList.contains('restarting')) {
        return; // 正在重启中，忽略点击
    }

    restartButton.classList.add('restarting');
    isRestarting = true;
    shouldReconnect = false;  // 禁用自动重连
    
    try {
        // 先关闭当前WebSocket连接
        if (ws) {
            console.log('Closing old WebSocket connection...');
            ws.close();
            ws = null;
        }
        
        // 清空本地数据
        allFinalTokens = [];
        currentNonFinalTokens = [];
        lastMergedIndex = 0; // 重置合并索引
        subtitleContainer.innerHTML = '<div style="text-align: center; padding: 40px; color: #6b7280;">Restarting recognition...</div>';
        
        // 等待连接完全关闭
        await new Promise(resolve => setTimeout(resolve, 500));
        
        // 调用后端重启接口
        const response = await fetch('/restart', { method: 'POST' });
        
        if (response.ok) {
            console.log('Recognition restarted successfully');
            // 等待后端完成重启
            await new Promise(resolve => setTimeout(resolve, 1500));
            
            // 重新启用自动重连并连接
            shouldReconnect = true;
            isRestarting = false;
            connect();
        } else {
            console.error('Failed to restart recognition');
            subtitleContainer.innerHTML = '<div style="text-align: center; padding: 40px; color: #ef4444;">Failed to restart. Please try again.</div>';
            shouldReconnect = true;  // 恢复自动重连
            isRestarting = false;
        }
    } catch (error) {
        console.error('Error restarting recognition:', error);
        subtitleContainer.innerHTML = '<div style="text-align: center; padding: 40px; color: #ef4444;">Connection error. Please try again.</div>';
        shouldReconnect = true;  // 恢复自动重连
        isRestarting = false;
    } finally {
        setTimeout(() => {
            restartButton.classList.remove('restarting');
        }, 1500);
    }
});

// 暂停/恢复识别功能
pauseButton.addEventListener('click', async () => {
    try {
        if (isPaused) {
            // 恢复识别
            const response = await fetch('/resume', { method: 'POST' });
            if (response.ok) {
                isPaused = false;
                pauseIcon.textContent = '⏸️';
                pauseButton.title = 'Pause recognition';
                console.log('Recognition resumed');
            }
        } else {
            // 暂停识别
            const response = await fetch('/pause', { method: 'POST' });
            if (response.ok) {
                isPaused = true;
                pauseIcon.textContent = '▶️';
                pauseButton.title = 'Resume recognition';
                console.log('Recognition paused');
            }
        }
    } catch (error) {
        console.error('Error toggling pause state:', error);
    }
});

if (audioSourceButton) {
    audioSourceButton.addEventListener('click', async () => {
        const nextSource = audioSource === 'system' ? 'microphone' : 'system';

        try {
            const response = await fetch('/audio-source', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ source: nextSource })
            });

            let result = null;
            try {
                result = await response.json();
            } catch (parseError) {
                console.error('Failed to parse audio source response:', parseError);
            }

            if (response.ok && result && result.source) {
                audioSource = result.source;
                updateAudioSourceButton();
                localStorage.setItem('audioSource', audioSource);
                if (result.message) {
                    console.log(result.message);
                } else {
                    console.log(`Audio source switched to ${audioSource}`);
                }
            } else {
                const message = result?.message || `Server responded with status ${response.status}`;
                console.error('Failed to switch audio source:', message);
            }
        } catch (error) {
            console.error('Error switching audio source:', error);
        }
    });
}




function displayErrorMessage(message) {
    subtitleContainer.innerHTML = `
        <div class="error-message-overlay">
            <h2 class="error-title">Error</h2>
            <p class="error-text">${escapeHtml(message)}</p>
            <p class="error-suggestion">Please set your SONIOX_API_KEY environment variable or check your network connection.</p>
        </div>
    `;
    subtitleContainer.scrollTop = 0; // Ensure error is visible
}

async function fetchApiKeyStatus() {
    try {
        const response = await fetch('/api-key-status');
        if (!response.ok) {
            console.error('Failed to fetch API key status:', response.statusText);
            return;
        }
        const data = await response.json();
        if (data.status === 'error' && data.message) {
            displayErrorMessage(data.message);
        }
    } catch (error) {
        console.error('Error fetching API key status:', error);
        // Do not display a generic network error here, as it might be a temporary server startup issue.
        // The WebSocket connection will eventually show the error if the API key is truly missing.
    }
}

async function fetchOscTranslationStatus() {
    if (!oscTranslationButton) {
        return;
    }

    try {
        const response = await fetch('/osc-translation');
        if (!response.ok) {
            return;
        }

        const data = await response.json();
        oscTranslationEnabled = !!data.enabled;
        updateOscTranslationButton();
    } catch (error) {
        console.error('Error fetching OSC translation status:', error);
    }
}


function connect() {
    ws = new WebSocket(`ws://${window.location.host}/ws`);

    ws.onopen = () => {
        console.log('WebSocket connected');
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleMessage(data);
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
    };

    ws.onclose = () => {
        console.log('WebSocket closed');
        
        // 只在应该重连且不在重启过程中时才重连
        if (shouldReconnect && !isRestarting) {
            console.log('Attempting to reconnect in 2 seconds...');
            setTimeout(connect, 2000);
        } else {
            console.log('Auto-reconnect disabled');
        }
    };
}

function handleMessage(data) {
    if (data.type === 'error') {
        displayErrorMessage(data.message);
        return;
    }
    if (data.type === 'clear') {
        // 清空所有数据
        console.log('Clearing all subtitles...');
        allFinalTokens = [];
        currentNonFinalTokens = [];
        lastMergedIndex = 0; // 重置合并索引
        // 不修改UI,因为重启流程会处理
        return;
    }
    
    if (data.type === 'update') {
        let separatorFromTokens = false;
        let hasNewFinalContent = false;
        if (data.final_tokens && data.final_tokens.length > 0) {
            data.final_tokens.forEach(token => {
                if (token.text === '<end>') {
                    separatorFromTokens = true;
                    pushSeparator('endpoint');
                    return;
                }
                hasNewFinalContent = true;
                insertFinalToken(token);
            });
        }
        
        // 更新non-final tokens并过滤 <end>
        currentNonFinalTokens = (data.non_final_tokens || []).filter(token => token.text !== '<end>');
        currentNonFinalTokens.forEach(assignSequenceIndex);
        
        let separatorAdded = separatorFromTokens;
        
        if (data.has_translation && hasNewFinalContent) {
            separatorAdded = true;
            pushSeparator('translation');
        }
        
        if (data.endpoint_detected) {
            separatorAdded = true;
            pushSeparator('endpoint');
        }
        
        if (separatorAdded) {
            currentNonFinalTokens = [];
        }
        
        // 合并新增的final tokens
        if (hasNewFinalContent) {
            mergeFinalTokens();
        }
        
        // 重新渲染
        renderSubtitles();
    }
}

function pushSeparator(type) {
    const separatorToken = {
        is_separator: true,
        is_final: true,
        separator_type: type
    };
    allFinalTokens.push(separatorToken);
}

function insertFinalToken(token) {
    assignSequenceIndex(token);
    allFinalTokens.push(token);
}

/**
 * 合并连续的final tokens以减少token数量
 * 只合并从lastMergedIndex开始的新tokens
 * 合并条件：相同speaker、相同language、相同translation_status、is_final=true、非分隔符
 */
function mergeFinalTokens() {
    if (allFinalTokens.length === 0) {
        return;
    }

    const safeStart = Math.max(0, lastMergedIndex - 1);
    const startIndex = Math.min(safeStart, allFinalTokens.length - 1);
    let writeIndex = startIndex;
    let readIndex = startIndex;

    while (readIndex < allFinalTokens.length) {
        const currentToken = allFinalTokens[readIndex];

        // 分隔符或非final token不合并，直接保留
        if (currentToken.is_separator || !currentToken.is_final) {
            allFinalTokens[writeIndex] = currentToken;
            writeIndex++;
            readIndex++;
            continue;
        }

        // 尝试合并连续的相似token
        let mergedText = currentToken.text || '';
        let mergedToken = { ...currentToken };
        let nextIndex = readIndex + 1;

        // 查找可以合并的后续tokens
        while (nextIndex < allFinalTokens.length) {
            const nextToken = allFinalTokens[nextIndex];

            // 检查是否可以合并
            if (
                !nextToken.is_separator &&
                nextToken.is_final &&
                nextToken.speaker === currentToken.speaker &&
                nextToken.language === currentToken.language &&
                (nextToken.translation_status || 'original') === (currentToken.translation_status || 'original') &&
                nextToken.source_language === currentToken.source_language
            ) {
                // 合并文本
                mergedText += (nextToken.text || '');
                nextIndex++;
            } else {
                // 遇到不能合并的token，停止
                break;
            }
        }

        // 更新合并后的token
        mergedToken.text = mergedText;
        mergedToken._merged = true; // 标记为已合并

        allFinalTokens[writeIndex] = mergedToken;
        writeIndex++;
        readIndex = nextIndex;
    }

    // 截断数组，移除已合并的重复项
    allFinalTokens.length = writeIndex;

    // 更新lastMergedIndex到新的末尾
    lastMergedIndex = allFinalTokens.length;
}

function getLanguageTag(language) {
    if (!language) return '';
    
    // 直接显示语言代码，支持任何语言
    return `<span class="language-tag">${language.toUpperCase()}</span>`;
}

function assignSequenceIndex(token) {
    if (!token || token._sequenceIndex !== undefined) {
        return;
    }
    token._sequenceIndex = tokenSequenceCounter++;
}

function isCloseToBottom() {
    return (subtitleContainer.scrollTop + subtitleContainer.clientHeight) >= (subtitleContainer.scrollHeight - SCROLL_STICKY_THRESHOLD);
}

function captureScrollState() {
    const wasAtBottom = isCloseToBottom();

    if (wasAtBottom) {
        return { wasAtBottom: true };
    }

    const sentenceBlocks = subtitleContainer.querySelectorAll('.sentence-block');
    const currentScrollTop = subtitleContainer.scrollTop;
    let anchor = null;

    for (const block of sentenceBlocks) {
        const blockTop = block.offsetTop;
        const blockBottom = blockTop + block.offsetHeight;
        if (blockBottom > currentScrollTop) {
            anchor = block;
            break;
        }
    }

    if (anchor) {
        return {
            wasAtBottom: false,
            sentenceId: anchor.dataset.sentenceId,
            offset: currentScrollTop - anchor.offsetTop
        };
    }

    return {
        wasAtBottom: false,
        scrollTop: currentScrollTop
    };
}

function restoreScrollState(state) {
    if (!state) {
        return;
    }

    if (state.wasAtBottom) {
        subtitleContainer.scrollTop = subtitleContainer.scrollHeight;
        return;
    }

    if (state.sentenceId) {
        const anchor = subtitleContainer.querySelector(`.sentence-block[data-sentence-id="${state.sentenceId}"]`);
        if (anchor) {
            subtitleContainer.scrollTop = anchor.offsetTop + (state.offset || 0);
            return;
        }
    }

    if (typeof state.scrollTop === 'number') {
        subtitleContainer.scrollTop = state.scrollTop;
    }
}

function getSpeakerClass(speaker) {
    if (speaker === null || speaker === undefined || speaker === 'undefined') {
        return 'speaker-undefined';
    }
    return `speaker-${speaker}`;
}

// 异步获取假名注音
async function getFuriganaHtml(text) {
    if (!text || !furiganaEnabled) {
        return null;
    }
    
    // 检查缓存
    if (furiganaCache.has(text)) {
        return furiganaCache.get(text);
    }
    
    try {
        const response = await fetch('/furigana', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });
        
        if (!response.ok) {
            return null;
        }
        
        const data = await response.json();
        if (data.status === 'ok' && data.html) {
            furiganaCache.set(text, data.html);
            return data.html;
        }
    } catch (error) {
        console.error('Failed to fetch furigana:', error);
    }
    
    return null;
}

function requestFurigana(text) {
    if (!text || !furiganaEnabled) {
        return;
    }

    if (furiganaCache.has(text) || pendingFuriganaRequests.has(text)) {
        return;
    }

    pendingFuriganaRequests.add(text);
    getFuriganaHtml(text)
        .then((html) => {
            if (html) {
                furiganaCache.set(text, html);
                renderSubtitles();
            }
        })
        .finally(() => {
            pendingFuriganaRequests.delete(text);
        });
}

function renderTokenSpan(token, useRubyHtml = null) {
    const classes = ['subtitle-text'];
    if (!token.is_final) {
        classes.push('non-final');
    }
    
    // 如果提供了 ruby HTML（假名注音），使用它
    if (useRubyHtml) {
        return `<span class="${classes.join(' ')}">${useRubyHtml}</span>`;
    }
    
    return `<span class="${classes.join(' ')}">${escapeHtml(token.text)}</span>`;
}

function getSentenceId(sentence, fallbackIndex) {
    const anchorToken = sentence.originalTokens[0] || sentence.translationTokens[0];
    if (anchorToken && anchorToken._sequenceIndex !== undefined) {
        return `sent-${anchorToken._sequenceIndex}`;
    }
    return `sent-fallback-${fallbackIndex}`;
}

function renderSubtitles() {
    const scrollState = captureScrollState();
    const tokens = [...allFinalTokens, ...currentNonFinalTokens];
    tokens.forEach(assignSequenceIndex);

    if (tokens.length === 0) {
        subtitleContainer.innerHTML = '<div class="empty-state">Subtitles will appear here...</div>';
        subtitleContainer.scrollTop = 0;
        autoStickToBottom = true;
        return;
    }

    const sentences = [];
    let currentSentence = null;
    let pendingTranslationSentence = null;

    const ensureSpeakerValue = (speaker) => {
        return (speaker === null || speaker === undefined) ? 'undefined' : speaker;
    };

    const startSentence = (speaker, options = {}) => {
        const normalizedSpeaker = ensureSpeakerValue(speaker);
        const sentence = {
            speaker: normalizedSpeaker,
            originalTokens: [],
            translationTokens: [],
            originalLang: null,
            translationLang: null,
            requiresTranslation: options.requiresTranslation !== undefined ? options.requiresTranslation : null, // null means undecided
            isTranslationOnly: !!options.translationOnly,
            hasFakeTranslation: false
        };
        sentences.push(sentence);
        if (!sentence.isTranslationOnly) {
            currentSentence = sentence;
        }
        return sentence;
    };

    const canAcceptTranslation = (sentence, token) => {
        if (!sentence) return false;
        if (sentence.hasFakeTranslation) return false;

        if (sentence.isTranslationOnly) {
            if (sentence.originalLang && token.source_language && sentence.originalLang !== token.source_language) {
                return false;
            }
            if (sentence.translationLang && token.language && sentence.translationLang !== token.language) {
                return false;
            }
            return true;
        }

        if (sentence.requiresTranslation === false) return false;

        if (token.source_language && sentence.originalLang && sentence.originalLang !== token.source_language) {
            return false;
        }

        if (sentence.translationLang && token.language && sentence.translationLang !== token.language) {
            return false;
        }

        return true;
    };

    const findLastSentenceForSpeaker = (speaker, predicate = () => true) => {
        const normalizedSpeaker = ensureSpeakerValue(speaker);
        for (let i = sentences.length - 1; i >= 0; i--) {
            const sentence = sentences[i];
            if (sentence.speaker === normalizedSpeaker && predicate(sentence)) {
                return sentence;
            }
        }
        return null;
    };

    tokens.forEach(token => {
        if (token.is_separator) {
            const separatorType = token.separator_type || 'translation';
            
            // 当遇到分隔符时，如果当前句子需要翻译但还没有译文，
            // 我们添加一个"假"的翻译标记，表示这个句子已经"完结"了。
            // 这样后续迟到的译文就不会匹配到这个已经完结的句子，而是会另起一行。
            if (currentSentence && currentSentence.requiresTranslation !== false && currentSentence.translationTokens.length === 0) {
                currentSentence.hasFakeTranslation = true;
            }

            if (separatorType === 'endpoint') {
                if (currentSentence) {
                    if (segmentMode === 'endpoint') {
                        currentSentence = null;
                    }
                }
            } else if (separatorType === 'translation') {
                if (segmentMode === 'translation') {
                    currentSentence = null;
                }
            }
            // 分隔符也会打断 pending 状态，迫使新的译文重新寻找匹配
            pendingTranslationSentence = null;
            return;
        }

        const speaker = ensureSpeakerValue(token.speaker);
        const translationStatus = token.translation_status || 'original';

        if (translationStatus === 'translation') {
            let targetSentence = null;

            // 1. 尝试匹配 pending
            if (pendingTranslationSentence && pendingTranslationSentence.speaker === speaker && canAcceptTranslation(pendingTranslationSentence, token)) {
                targetSentence = pendingTranslationSentence;
            }

            // 2. 尝试匹配该说话人最近的一个可接受译文的句子
            if (!targetSentence) {
                targetSentence = findLastSentenceForSpeaker(speaker, (sentence) => canAcceptTranslation(sentence, token));
            }

            // 3. 如果都匹配不到，创建一个纯译文句子
            if (!targetSentence) {
                targetSentence = startSentence(speaker, { translationOnly: true });
            }

            if (targetSentence.translationLang === null && token.language) {
                targetSentence.translationLang = token.language;
            }

            if (!targetSentence.originalLang && token.source_language) {
                targetSentence.originalLang = token.source_language;
            }

            targetSentence.translationTokens.push(token);
            pendingTranslationSentence = targetSentence;
        } else {
            // 原文 token (original 或 none)
            const tokenRequiresTranslation = (translationStatus !== 'none');

            // 检查是否需要新起一个句子
            let shouldStartNew = false;
            if (!currentSentence) shouldStartNew = true;
            else if (currentSentence.speaker !== speaker) shouldStartNew = true;
            else if (currentSentence.isTranslationOnly) shouldStartNew = true;
            else if (currentSentence.requiresTranslation !== null && currentSentence.requiresTranslation !== tokenRequiresTranslation) {
                // 如果当前句子的翻译需求状态与新token不一致（例如从 original 变 none），则新起一句
                shouldStartNew = true;
            }

            if (shouldStartNew) {
                currentSentence = startSentence(speaker, { requiresTranslation: tokenRequiresTranslation });
            }

            // 确保状态被设置（如果是新句子且 options 没传，或者 null 的情况）
            if (currentSentence.requiresTranslation === null) {
                currentSentence.requiresTranslation = tokenRequiresTranslation;
            }

            if (currentSentence.originalLang === null && token.language) {
                currentSentence.originalLang = token.language;
            } else if (currentSentence.originalLang && token.language && currentSentence.originalLang !== token.language) {
                // 语言变了，新起一句
                currentSentence = startSentence(speaker, { requiresTranslation: tokenRequiresTranslation });
                currentSentence.originalLang = token.language;
            }

            currentSentence.originalTokens.push(token);
        }
    });

    const showOriginal = (displayMode === 'both' || displayMode === 'original');
    const showTranslation = (displayMode === 'both' || displayMode === 'translation');

    const speakerBlocks = [];
    let currentBlock = null;

    sentences.forEach(sentence => {
        const hasOriginal = showOriginal && sentence.originalTokens.length > 0;
        const hasTranslation = showTranslation && sentence.translationTokens.length > 0;

        if (!hasOriginal && !hasTranslation) {
            return;
        }

        if (!currentBlock || currentBlock.speaker !== sentence.speaker) {
            if (currentBlock) {
                speakerBlocks.push(currentBlock);
            }
            currentBlock = { speaker: sentence.speaker, sentences: [] };
        }

        currentBlock.sentences.push(sentence);
    });

    if (currentBlock) {
        speakerBlocks.push(currentBlock);
    }

    if (speakerBlocks.length === 0) {
        subtitleContainer.innerHTML = '<div class="empty-state">Subtitles will appear here...</div>';
        restoreScrollState(scrollState);
        autoStickToBottom = scrollState ? scrollState.wasAtBottom : true;
        return;
    }

    let html = '';
    let previousSpeaker = null;
    let fallbackCounter = 0;
    const activeSentenceIds = new Set();
    const pendingSentenceUpdates = [];
    const sentencesToRemove = [];
    let blockingUpdate = false;

    for (const block of speakerBlocks) {
        if (blockingUpdate) {
            break;
        }

        let blockHtml = '';

        if (block.speaker !== previousSpeaker) {
            blockHtml += `<div class="speaker-label ${getSpeakerClass(block.speaker)}">SPEAKER ${block.speaker}</div>`;
        }

        const sentencesHtml = [];

        for (const sentence of block.sentences) {
            const sentenceId = getSentenceId(sentence, fallbackCounter++);
            activeSentenceIds.add(sentenceId);

            const sentenceParts = [];

            if (showOriginal && sentence.originalTokens.length > 0) {
                const langTag = getLanguageTag(sentence.originalLang);
                const isJapanese = sentence.originalLang === 'ja';

                if (isJapanese && furiganaEnabled) {
                    const plainText = sentence.originalTokens.map(t => t.text).join('');
                    const hasNonFinal = sentence.originalTokens.some(t => !t.is_final);

                    if (plainText.trim().length === 0) {
                        const lineContent = sentence.originalTokens.map(t => renderTokenSpan(t)).join('');
                        sentenceParts.push(`<div class="subtitle-line original-line">${langTag}${lineContent}</div>`);
                    } else {
                        const rubyHtml = furiganaCache.get(plainText);

                        if (rubyHtml) {
                            const classes = ['subtitle-text'];
                            if (hasNonFinal) {
                                classes.push('non-final');
                            }
                            const rubySpan = `<span class="${classes.join(' ')}">${rubyHtml}</span>`;
                            sentenceParts.push(`<div class="subtitle-line original-line">${langTag}${rubySpan}</div>`);
                        } else {
                            requestFurigana(plainText);
                            const previousHtml = renderedSentences.get(sentenceId);
                            if (previousHtml) {
                                sentencesHtml.push(previousHtml);
                            } else {
                                blockingUpdate = true;
                            }
                            continue;
                        }
                    }
                } else {
                    const lineContent = sentence.originalTokens.map(t => renderTokenSpan(t)).join('');
                    sentenceParts.push(`<div class="subtitle-line original-line">${langTag}${lineContent}</div>`);
                }
            }

            if (blockingUpdate) {
                break;
            }

            if (showTranslation && sentence.translationTokens.length > 0) {
                const langTag = getLanguageTag(sentence.translationLang);
                const lineContent = sentence.translationTokens.map(t => renderTokenSpan(t)).join('');
                sentenceParts.push(`<div class="subtitle-line">${langTag}${lineContent}</div>`);
            }

            if (sentenceParts.length === 0) {
                sentencesToRemove.push(sentenceId);
                continue;
            }

            const sentenceHtml = `<div class="sentence-block" data-sentence-id="${sentenceId}">${sentenceParts.join('')}</div>`;
            sentencesHtml.push(sentenceHtml);
            pendingSentenceUpdates.push({ id: sentenceId, html: sentenceHtml });
        }

        if (blockingUpdate) {
            break;
        }

        if (sentencesHtml.length > 0) {
            blockHtml += sentencesHtml.join('');
        }

        if (blockHtml.trim().length > 0) {
            const blockClass = (block.speaker === previousSpeaker) ? 'subtitle-block same-speaker' : 'subtitle-block';
            html += `<div class="${blockClass}">${blockHtml}</div>`;
            previousSpeaker = block.speaker;
        }
    }

    if (blockingUpdate) {
        return;
    }

    pendingSentenceUpdates.forEach(({ id, html }) => renderedSentences.set(id, html));
    sentencesToRemove.forEach(id => renderedSentences.delete(id));

    renderedSentences.forEach((_, key) => {
        if (!activeSentenceIds.has(key)) {
            renderedSentences.delete(key);
        }
    });

    if (!html) {
        subtitleContainer.innerHTML = '<div class="empty-state">Subtitles will appear here...</div>';
        restoreScrollState(scrollState);
        autoStickToBottom = scrollState ? scrollState.wasAtBottom : true;
        return;
    }

    // 增量渲染：解析新生成的 html 到临时容器，然后只更新发生变化的 .sentence-block
    const frag = document.createElement('div');
    frag.innerHTML = html;

    // 如果页面中存在占位 empty-state（"Subtitles will appear here..."），当有真实字幕时应移除
    const emptyNodes = subtitleContainer.querySelectorAll('.empty-state');
    emptyNodes.forEach(node => node.remove());

    // 更通用的清理：移除 subtitleContainer 中所有非字幕占位元素（例如重启提示、Server Closed 等）
    // 保留已有的 `.subtitle-block` 或包含 `.sentence-block` 的节点，删除其它直接子节点
    Array.from(subtitleContainer.children).forEach(child => {
        if (child.classList && child.classList.contains('subtitle-block')) {
            return; // 保留 subtitle-block
        }
        if (child.querySelector && child.querySelector('.sentence-block')) {
            return; // 保留包含句子块的容器
        }
        // 否则认为是占位/状态节点，移除
        child.remove();
    });

    try {
        // 以 subtitle-block 为单位进行增量更新，保证 speaker label 与分块结构被保留
        const newBlocks = Array.from(frag.querySelectorAll('.subtitle-block'));
        const existingBlocks = Array.from(subtitleContainer.querySelectorAll('.subtitle-block'));

        // 索引现有块，键为 data-block-id（若不存在则使用首个 sentence 的 id 作为块 id）
        const existingIndex = new Map();
        existingBlocks.forEach((node, idx) => {
            let id = node.dataset.blockId;
            if (!id) {
                const firstSent = node.querySelector('.sentence-block');
                if (firstSent && firstSent.dataset.sentenceId) {
                    id = `block-${firstSent.dataset.sentenceId}`;
                } else {
                    id = `block-fallback-${idx}`;
                }
                node.dataset.blockId = id;
            }
            existingIndex.set(id, node);
        });

        const keepIds = new Set();

        // 遍历新的 subtitle-block，比较并替换/插入
        for (let i = 0; i < newBlocks.length; i++) {
            const newBlock = newBlocks[i];
            // 为新块生成稳定 id（基于其首个 sentence 的 id）
            let id = newBlock.dataset.blockId;
            if (!id) {
                const firstSent = newBlock.querySelector('.sentence-block');
                if (firstSent && firstSent.dataset.sentenceId) {
                    id = `block-${firstSent.dataset.sentenceId}`;
                } else {
                    id = `block-fallback-${i}`;
                }
                newBlock.dataset.blockId = id;
            }

            const newHtml = newBlock.innerHTML;
            const existingNode = existingIndex.get(id);

            if (existingNode) {
                // 内容相同则跳过
                if (renderedBlocks.get(id) === newHtml) {
                    keepIds.add(id);
                    continue;
                }
                // 替换整个 subtitle-block 节点（保留新的 speaker label 和结构）
                const wrapper = document.createElement('div');
                wrapper.className = newBlock.className || 'subtitle-block';
                wrapper.dataset.blockId = id;
                wrapper.innerHTML = newHtml;
                existingNode.replaceWith(wrapper);
                renderedBlocks.set(id, newHtml);
                keepIds.add(id);
            } else {
                // 新的 subtitle-block，需要插入：尝试按新Blocks 中下一个已有块定位插入点
                const wrapper = document.createElement('div');
                wrapper.className = newBlock.className || 'subtitle-block';
                wrapper.dataset.blockId = id;
                wrapper.innerHTML = newHtml;

                let inserted = false;
                for (let j = i + 1; j < newBlocks.length; j++) {
                    const nextFirst = newBlocks[j].querySelector('.sentence-block');
                    const nextId = nextFirst && nextFirst.dataset.sentenceId ? `block-${nextFirst.dataset.sentenceId}` : newBlocks[j].dataset.blockId;
                    if (!nextId) continue;
                    const nextExisting = subtitleContainer.querySelector(`.subtitle-block[data-block-id="${nextId}"]`);
                    if (nextExisting) {
                        subtitleContainer.insertBefore(wrapper, nextExisting);
                        inserted = true;
                        break;
                    }
                }
                if (!inserted) {
                    subtitleContainer.appendChild(wrapper);
                }
                renderedBlocks.set(id, newHtml);
                keepIds.add(id);
            }
        }

        // 移除旧的、不再需要的块
        existingBlocks.forEach(node => {
            const id = node.dataset.blockId || (node.querySelector('.sentence-block') ? `block-${node.querySelector('.sentence-block').dataset.sentenceId}` : null);
            if (id && !keepIds.has(id)) {
                node.remove();
                renderedBlocks.delete(id);
            }
        });

    } catch (e) {
        // 在任何异常情况下回退到全量替换，保证正确性
        console.warn('Incremental render (block-level) failed, falling back to full render:', e);
        subtitleContainer.innerHTML = html;
        // 同步缓存为当前 DOM
        renderedBlocks.clear();
        const allBlocks = subtitleContainer.querySelectorAll('.subtitle-block');
        allBlocks.forEach((node, idx) => {
            let id = node.dataset.blockId;
            if (!id) {
                const first = node.querySelector('.sentence-block');
                id = first && first.dataset.sentenceId ? `block-${first.dataset.sentenceId}` : `block-fallback-${idx}`;
                node.dataset.blockId = id;
            }
            renderedBlocks.set(id, node.innerHTML);
        });
    }

    // 恢复滚动状态并处理自动贴底
    restoreScrollState(scrollState);
    autoStickToBottom = scrollState ? scrollState.wasAtBottom : isCloseToBottom();
    if (autoStickToBottom) {
        subtitleContainer.scrollTop = subtitleContainer.scrollHeight;
    }
}

subtitleContainer.addEventListener('scroll', () => {
    autoStickToBottom = isCloseToBottom();
});

window.addEventListener('resize', () => {
    if (autoStickToBottom) {
        subtitleContainer.scrollTop = subtitleContainer.scrollHeight;
    }
});

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

document.addEventListener('DOMContentLoaded', () => {
    fetchApiKeyStatus();
    fetchOscTranslationStatus();
    connect();
});