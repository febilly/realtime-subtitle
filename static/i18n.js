(function (global) {
    'use strict';

    const MESSAGES = {
        en: {
            empty_state: 'Subtitles will appear here...',
            theme_toggle: 'Toggle theme',
            restart: 'Restart recognition',
            auto_restart_on: 'Auto restart enabled (click to disable)',
            auto_restart_off: 'Auto restart recognition on disconnect',
            pause_resume: 'Pause/Resume recognition',
            pause: 'Pause recognition',
            resume: 'Resume recognition',
            audio_to_system: 'Switch to system audio capture',
            audio_to_mic: 'Switch to microphone capture',
            segment_translation: 'Segment by translation (click to switch to endpoint mode)',
            segment_endpoint: 'Segment by endpoint (click to switch to translation mode)',
            display_both: 'Show both original and translation',
            display_original: 'Show original only',
            display_translation: 'Show translation only',
            osc_on: 'Sending translation to VRChat (click to disable)',
            osc_off: 'Send translation to VRChat via OSC',
            furigana_on: 'Furigana enabled (click to disable)',
            furigana_off: 'Furigana disabled (click to enable)',
            bottom_safe_area_on: 'Hide extra bottom space (mobile)',
            bottom_safe_area_off: 'Show extra bottom space (mobile)',
            error_title: 'Error',
            error_suggestion_api: 'Please set your SONIOX_API_KEY environment variable or check your network connection.',
            restarting: 'Restarting recognition...',
            connection_error_try_again: 'Connection error. Please try again.',
            restart_failed_try_again: 'Failed to restart. Please try again.',
            speaker_label: 'SPEAKER {speaker}',

            backend_manual_restart_disabled: 'Manual restart is disabled by server config',
            backend_pause_disabled: 'Pause is disabled by server config',
            backend_resume_disabled: 'Resume is disabled by server config',
            backend_audio_source_disabled: 'Audio source switching is disabled by server config',
            backend_osc_disabled: 'OSC translation toggle is disabled by server config',
            backend_furigana_unavailable: 'Furigana feature not available',
        },
        zh: {
            empty_state: '字幕将显示在这里…',
            theme_toggle: '切换主题',
            restart: '重启识别',
            auto_restart_on: '断线自动重启已开启（点击关闭）',
            auto_restart_off: '断线自动重启识别',
            pause_resume: '暂停/继续识别',
            pause: '暂停识别',
            resume: '继续识别',
            audio_to_system: '切换到系统音频采集',
            audio_to_mic: '切换到麦克风采集',
            segment_translation: '按翻译分段（点击切换到端点分段）',
            segment_endpoint: '按端点分段（点击切换到翻译分段）',
            display_both: '显示原文和翻译',
            display_original: '仅显示原文',
            display_translation: '仅显示翻译',
            osc_on: '正在发送翻译到 VRChat（点击关闭）',
            osc_off: '通过 OSC 发送翻译到 VRChat',
            furigana_on: '假名注音已开启（点击关闭）',
            furigana_off: '假名注音已关闭（点击开启）',
            bottom_safe_area_on: '隐藏底部留白（手机）',
            bottom_safe_area_off: '显示底部留白（手机）',
            error_title: '错误',
            error_suggestion_api: '请设置 SONIOX_API_KEY 环境变量，或检查网络连接。',
            restarting: '正在重启识别…',
            connection_error_try_again: '连接错误，请重试。',
            restart_failed_try_again: '重启失败，请重试。',
            speaker_label: '说话人 {speaker}',

            backend_manual_restart_disabled: '服务器已禁用手动重启',
            backend_pause_disabled: '服务器已禁用暂停',
            backend_resume_disabled: '服务器已禁用继续',
            backend_audio_source_disabled: '服务器已禁用切换音频源',
            backend_osc_disabled: '服务器已禁用 OSC 翻译开关',
                        translation_language: 'Translation language',
                        error_title: 'Error',
        },
        ja: {
            empty_state: '字幕はここに表示されます…',
            theme_toggle: 'テーマを切り替え',
            restart: '認識を再起動',
            auto_restart_on: '自動再起動が有効（クリックで無効）',
            auto_restart_off: '切断時に自動で再起動',
            pause_resume: '一時停止/再開',
            pause: '認識を一時停止',
            resume: '認識を再開',
            audio_to_system: 'システム音声キャプチャに切り替え',
            audio_to_mic: 'マイクキャプチャに切り替え',
            segment_translation: '翻訳で分割（クリックでエンドポイント分割へ）',
            segment_endpoint: 'エンドポイントで分割（クリックで翻訳分割へ）',
            display_both: '原文と翻訳を表示',
            display_original: '原文のみ表示',
            display_translation: '翻訳のみ表示',
            osc_on: '翻訳を VRChat に送信中（クリックで停止）',
            osc_off: 'OSC で翻訳を VRChat に送信',
            furigana_on: 'ふりがな有効（クリックで無効）',
            furigana_off: 'ふりがな無効（クリックで有効）',
            bottom_safe_area_on: '下部の余白を非表示（モバイル）',
            bottom_safe_area_off: '下部の余白を表示（モバイル）',
            error_title: 'エラー',
            error_suggestion_api: 'SONIOX_API_KEY 環境変数を設定するか、ネットワーク接続を確認してください。',
            restarting: '認識を再起動中…',
            connection_error_try_again: '接続エラーです。もう一度お試しください。',
            restart_failed_try_again: '再起動に失敗しました。もう一度お試しください。',
            speaker_label: '話者 {speaker}',
                        translation_language: '翻译语言',

            backend_manual_restart_disabled: 'サーバー設定により手動再起動は無効です',
            backend_pause_disabled: 'サーバー設定により一時停止は無効です',
            backend_resume_disabled: 'サーバー設定により再開は無効です',
            backend_audio_source_disabled: 'サーバー設定により音声ソース切替は無効です',
            backend_osc_disabled: 'サーバー設定により OSC 翻訳切替は無効です',
            backend_furigana_unavailable: 'ふりがな機能は利用できません',
        },
        ko: {
            empty_state: '자막이 여기에 표시됩니다…',
            theme_toggle: '테마 전환',
            restart: '인식 재시작',
            auto_restart_on: '자동 재시작 켜짐(클릭하여 끄기)',
            auto_restart_off: '연결 끊김 시 자동 재시작',
            pause_resume: '일시정지/재개',
            pause: '인식 일시정지',
            resume: '인식 재개',
            audio_to_system: '시스템 오디오 캡처로 전환',
            audio_to_mic: '마이크 캡처로 전환',
            segment_translation: '번역 기준 분할(클릭하여 엔드포인트 분할)',
            segment_endpoint: '엔드포인트 기준 분할(클릭하여 번역 분할)',
            display_both: '원문+번역 표시',
            display_original: '원문만 표시',
            display_translation: '번역만 표시',
            osc_on: 'VRChat으로 번역 전송 중(클릭하여 끄기)',
            osc_off: 'OSC로 VRChat에 번역 전송',
            furigana_on: '후리가나 켜짐(클릭하여 끄기)',
            furigana_off: '후리가나 꺼짐(클릭하여 켜기)',
            bottom_safe_area_on: '하단 여백 숨기기(모바일)',
                        translation_language: '翻訳言語',
            bottom_safe_area_off: '하단 여백 표시(모바일)',
            error_title: '오류',
            error_suggestion_api: 'SONIOX_API_KEY 환경 변수를 설정하거나 네트워크 연결을 확인하세요.',
            restarting: '인식을 재시작하는 중…',
            connection_error_try_again: '연결 오류입니다. 다시 시도하세요.',
            restart_failed_try_again: '재시작에 실패했습니다. 다시 시도하세요.',
            speaker_label: '화자 {speaker}',

            backend_manual_restart_disabled: '서버 설정으로 수동 재시작이 비활성화되었습니다',
            backend_pause_disabled: '서버 설정으로 일시정지가 비활성화되었습니다',
            backend_resume_disabled: '서버 설정으로 재개가 비활성화되었습니다',
            backend_audio_source_disabled: '서버 설정으로 오디오 소스 전환이 비활성화되었습니다',
            backend_osc_disabled: '서버 설정으로 OSC 번역 토글이 비활성화되었습니다',
            backend_furigana_unavailable: '후리가나 기능을 사용할 수 없습니다',
        },
        ru: {
            empty_state: 'Субтитры появятся здесь…',
            theme_toggle: 'Переключить тему',
            restart: 'Перезапустить распознавание',
            auto_restart_on: 'Автоперезапуск включён (нажмите, чтобы выключить)',
            auto_restart_off: 'Автоперезапуск при разрыве соединения',
            pause_resume: 'Пауза/Возобновить',
            pause: 'Пауза распознавания',
            resume: 'Возобновить распознавание',
            audio_to_system: 'Переключить на захват системного звука',
            audio_to_mic: 'Переключить на захват микрофона',
            segment_translation: 'Сегментация по переводу (нажмите для сегментации по endpoint)',
            segment_endpoint: 'Сегментация по endpoint (нажмите для сегментации по переводу)',
            display_both: 'Показывать оригинал и перевод',
                        translation_language: '번역 언어',
            display_original: 'Показывать только оригинал',
            display_translation: 'Показывать только перевод',
            osc_on: 'Отправка перевода в VRChat (нажмите, чтобы выключить)',
            osc_off: 'Отправлять перевод в VRChat через OSC',
            furigana_on: 'Фуригана включена (нажмите, чтобы выключить)',
            furigana_off: 'Фуригана выключена (нажмите, чтобы включить)',
            bottom_safe_area_on: 'Скрыть нижний отступ (моб.)',
            bottom_safe_area_off: 'Показать нижний отступ (моб.)',
            error_title: 'Ошибка',
            error_suggestion_api: 'Установите переменную окружения SONIOX_API_KEY или проверьте подключение к сети.',
            restarting: 'Перезапуск распознавания…',
            connection_error_try_again: 'Ошибка соединения. Повторите попытку.',
            restart_failed_try_again: 'Не удалось перезапустить. Повторите попытку.',
            speaker_label: 'Говорящий {speaker}',

            backend_manual_restart_disabled: 'Ручной перезапуск отключён настройками сервера',
            backend_pause_disabled: 'Пауза отключена настройками сервера',
            backend_resume_disabled: 'Возобновление отключено настройками сервера',
            backend_audio_source_disabled: 'Переключение источника звука отключено настройками сервера',
            backend_osc_disabled: 'Переключатель OSC-перевода отключён настройками сервера',
            backend_furigana_unavailable: 'Функция фуриганы недоступна',
        },
        es: {
            empty_state: 'Los subtítulos aparecerán aquí…',
            theme_toggle: 'Cambiar tema',
            restart: 'Reiniciar reconocimiento',
            auto_restart_on: 'Reinicio automático activado (clic para desactivar)',
            auto_restart_off: 'Reiniciar automáticamente al desconectarse',
            pause_resume: 'Pausar/Reanudar',
                        translation_language: 'Язык перевода',
            pause: 'Pausar reconocimiento',
            resume: 'Reanudar reconocimiento',
            audio_to_system: 'Cambiar a captura de audio del sistema',
            audio_to_mic: 'Cambiar a captura de micrófono',
            segment_translation: 'Segmentar por traducción (clic para segmentar por endpoint)',
            segment_endpoint: 'Segmentar por endpoint (clic para segmentar por traducción)',
            display_both: 'Mostrar original y traducción',
            display_original: 'Mostrar solo original',
            display_translation: 'Mostrar solo traducción',
            osc_on: 'Enviando traducción a VRChat (clic para desactivar)',
            osc_off: 'Enviar traducción a VRChat vía OSC',
            furigana_on: 'Furigana activada (clic para desactivar)',
            furigana_off: 'Furigana desactivada (clic para activar)',
            bottom_safe_area_on: 'Ocultar espacio inferior (móvil)',
            bottom_safe_area_off: 'Mostrar espacio inferior (móvil)',
            error_title: 'Error',
            error_suggestion_api: 'Configura la variable de entorno SONIOX_API_KEY o revisa tu conexión de red.',
            restarting: 'Reiniciando reconocimiento…',
            connection_error_try_again: 'Error de conexión. Inténtalo de nuevo.',
            restart_failed_try_again: 'No se pudo reiniciar. Inténtalo de nuevo.',
            speaker_label: 'Hablante {speaker}',

            backend_manual_restart_disabled: 'El reinicio manual está deshabilitado por configuración del servidor',
            backend_pause_disabled: 'La pausa está deshabilitada por configuración del servidor',
            backend_resume_disabled: 'La reanudación está deshabilitada por configuración del servidor',
            backend_audio_source_disabled: 'El cambio de fuente de audio está deshabilitado por configuración del servidor',
            backend_osc_disabled: 'El interruptor de traducción OSC está deshabilitado por configuración del servidor',
            backend_furigana_unavailable: 'La función de furigana no está disponible',
        },
                        translation_language: 'Idioma de traducción',
        pt: {
            empty_state: 'As legendas aparecerão aqui…',
            theme_toggle: 'Alternar tema',
            restart: 'Reiniciar reconhecimento',
            auto_restart_on: 'Reinício automático ativado (clique para desativar)',
            auto_restart_off: 'Reiniciar automaticamente ao desconectar',
            pause_resume: 'Pausar/Retomar',
            pause: 'Pausar reconhecimento',
            resume: 'Retomar reconhecimento',
            audio_to_system: 'Alternar para captura de áudio do sistema',
            audio_to_mic: 'Alternar para captura do microfone',
            segment_translation: 'Segmentar por tradução (clique para segmentar por endpoint)',
            segment_endpoint: 'Segmentar por endpoint (clique para segmentar por tradução)',
            display_both: 'Mostrar original e tradução',
            display_original: 'Mostrar apenas original',
            display_translation: 'Mostrar apenas tradução',
            osc_on: 'Enviando tradução para o VRChat (clique para desativar)',
            osc_off: 'Enviar tradução para o VRChat via OSC',
            furigana_on: 'Furigana ativada (clique para desativar)',
            furigana_off: 'Furigana desativada (clique para ativar)',
            bottom_safe_area_on: 'Ocultar espaço inferior (mobile)',
            bottom_safe_area_off: 'Mostrar espaço inferior (mobile)',
            error_title: 'Erro',
            error_suggestion_api: 'Defina a variável de ambiente SONIOX_API_KEY ou verifique sua conexão de rede.',
            restarting: 'Reiniciando reconhecimento…',
            connection_error_try_again: 'Erro de conexão. Tente novamente.',
            restart_failed_try_again: 'Falha ao reiniciar. Tente novamente.',
            speaker_label: 'Falante {speaker}',

                        translation_language: 'Idioma da tradução',
            backend_manual_restart_disabled: 'O reinício manual está desativado pela configuração do servidor',
            backend_pause_disabled: 'A pausa está desativada pela configuração do servidor',
            backend_resume_disabled: 'A retomada está desativada pela configuração do servidor',
            backend_audio_source_disabled: 'A troca de fonte de áudio está desativada pela configuração do servidor',
            backend_osc_disabled: 'O alternador de tradução OSC está desativado pela configuração do servidor',
            backend_furigana_unavailable: 'O recurso de furigana não está disponível',
        },
    };

    function normalizeLang(input) {
        const raw = (input || '').toLowerCase();
        const base = raw.split('-')[0];
        if (base === 'zh') return 'zh';
        if (base === 'ja') return 'ja';
        if (base === 'ko') return 'ko';
        if (base === 'ru') return 'ru';
        if (base === 'es') return 'es';
        if (base === 'pt') return 'pt';
        return 'en';
    }

    function getBrowserLang() {
        const candidates = [];
        if (navigator.languages && Array.isArray(navigator.languages)) {
            candidates.push(...navigator.languages);
        }
        if (navigator.language) {
            candidates.push(navigator.language);
        }
        if (navigator.userLanguage) {
            candidates.push(navigator.userLanguage);
        }

        for (const candidate of candidates) {
            const lang = normalizeLang(candidate);
            if (lang && MESSAGES[lang]) {
                return lang;
            }
        }
        return 'en';
    }

    function format(template, vars) {
        if (!vars) return template;
        return template.replace(/\{(\w+)\}/g, function (_m, key) {
            const value = vars[key];
            return value === undefined || value === null ? '' : String(value);
        });
    }

    const LANG = getBrowserLang();

    function t(key, vars) {
        const table = MESSAGES[LANG] || MESSAGES.en;
        const fallback = MESSAGES.en;
        const template = (table && table[key]) || (fallback && fallback[key]) || key;
        return format(template, vars);
    }

    global.I18N = {
        lang: LANG,
        t,
    };
})(window);
