import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from module_stubs import patch_modules, pop_module, set_module


def async_test(coro):
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro(*args, **kwargs))
        finally:
            loop.close()
    return wrapper


class TestFeaturePacksServer:
    @pytest.fixture(autouse=True)
    def restore_sys_modules(self):
        orig_config = sys.modules.get("realtime_subtitle.core.config")
        orig_web_server = pop_module("realtime_subtitle.web.web_server")
        yield
        if orig_config is not None:
            set_module("realtime_subtitle.core.config", orig_config)
        else:
            pop_module("realtime_subtitle.core.config")
        if orig_web_server is not None:
            set_module("realtime_subtitle.web.web_server", orig_web_server)
        else:
            pop_module("realtime_subtitle.web.web_server")

    def _create_server(self, module, stored_settings=None):
        session = MagicMock()
        session.is_paused = False
        session.get_audio_source.return_value = "system"
        with patch.object(module.local_store, "load", return_value=stored_settings if stored_settings is not None else {}):
            server = module.WebServer(session, MagicMock())
        return server

    def test_initial_feature_packs_for_new_and_upgraded_installs(self):
        with patch_modules({
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "realtime_subtitle.core.config": MagicMock(),
            "realtime_subtitle.llm.llm_client": MagicMock(),
        }):
            import realtime_subtitle.web.web_server as module

            # Brand-new install: empty local store -> all packs disabled
            new_server = self._create_server(module, {})
            assert new_server.get_feature_packs_state() == {
                "vrchat": False,
                "learning": False,
                "obs": False,
                "transcript": False,
            }

            # Existing user upgrading without featurePacks.v1 -> vrchat & learning enabled
            upgrade_server = self._create_server(module, {"theme": "dark"})
            assert upgrade_server.get_feature_packs_state() == {
                "vrchat": True,
                "learning": True,
                "obs": False,
                "transcript": False,
            }

            # Stored confirmed selection -> uses saved booleans verbatim
            confirmed_server = self._create_server(module, {
                "featurePacks.v1": json.dumps({
                    "version": 1,
                    "confirmed": True,
                    "isUpgrade": True,
                    "enabled": {"vrchat": False, "learning": True, "obs": True, "transcript": False},
                }),
            })
            assert confirmed_server.get_feature_packs_state() == {
                "vrchat": False,
                "learning": True,
                "obs": True,
                "transcript": False,
            }

    @async_test
    async def test_feature_packs_get_and_post_handlers_and_broadcast(self):
        with patch_modules({
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "realtime_subtitle.core.config": MagicMock(),
            "realtime_subtitle.llm.llm_client": MagicMock(),
        }):
            import realtime_subtitle.web.web_server as module

            module.web.json_response.side_effect = lambda data, status=200: (data, status)
            server = self._create_server(module, {})
            server.broadcast_to_clients = AsyncMock()

            get_req = AsyncMock()
            get_req.remote = "127.0.0.1"
            data, status = await server.feature_packs_get_handler(get_req)
            assert status == 200
            assert data == {
                "status": "ok",
                "packs": {"vrchat": False, "learning": False, "obs": False, "transcript": False},
            }

            # Non-loopback POST is rejected
            remote_req = AsyncMock()
            remote_req.remote = "10.0.0.5"
            remote_req.path = "/feature-packs"
            data, status = await server.feature_packs_post_handler(remote_req)
            assert status == 403

            # Invalid JSON returns 400
            bad_req = AsyncMock()
            bad_req.remote = "127.0.0.1"
            bad_req.json.side_effect = ValueError("bad json")
            data, status = await server.feature_packs_post_handler(bad_req)
            assert status == 400

            # Valid update updates state, ignores non-bool/unknown keys, and broadcasts
            post_req = AsyncMock()
            post_req.remote = "127.0.0.1"
            post_req.json.return_value = {
                "packs": {
                    "vrchat": True,
                    "learning": True,
                    "obs": "invalid",
                    "unknown": True,
                },
            }
            data, status = await server.feature_packs_post_handler(post_req)
            assert status == 200
            expected = {"vrchat": True, "learning": True, "obs": False, "transcript": False}
            assert data == {"status": "ok", "packs": expected}
            assert server.get_feature_packs_state() == expected
            assert server.is_pack_enabled("vrchat") is True
            assert server.is_pack_enabled("obs") is False
            server.broadcast_to_clients.assert_awaited_once_with({
                "type": "feature_packs",
                "packs": expected,
            })

    @async_test
    async def test_websocket_sends_initial_feature_packs_frame(self):
        with patch_modules({
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "realtime_subtitle.core.config": MagicMock(),
            "realtime_subtitle.llm.llm_client": MagicMock(),
        }):
            import realtime_subtitle.web.web_server as module

            async def _empty_aiter(_self=None):
                if False:
                    yield None

            fake_ws = AsyncMock()
            fake_ws.__aiter__ = _empty_aiter
            module.web.WebSocketResponse.return_value = fake_ws

            server = self._create_server(module, {
                "featurePacks.v1": json.dumps({
                    "version": 1,
                    "confirmed": True,
                    "isUpgrade": False,
                    "enabled": {"vrchat": False, "learning": True, "obs": False, "transcript": False},
                }),
            })
            req = MagicMock()
            req.query = {}
            req.remote = "127.0.0.1"
            await server.websocket_handler(req)

            sent_payloads = [json.loads(call.args[0]) for call in fake_ws.send_str.await_args_list]
            pack_frames = [frame for frame in sent_payloads if frame.get("type") == "feature_packs"]
            assert pack_frames == [{
                "type": "feature_packs",
                "packs": {"vrchat": False, "learning": True, "obs": False, "transcript": False},
            }]
            lang_frames = [frame for frame in sent_payloads if frame.get("type") == "learning_languages"]
            assert lang_frames == [{
                "type": "learning_languages",
                "languages": {"ja": True, "zh": True, "ko": True},
                "initialized": False,
            }]

    @async_test
    async def test_learning_languages_initial_get_post_broadcast_and_ws(self):
        with patch_modules({
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "realtime_subtitle.core.config": MagicMock(),
            "realtime_subtitle.llm.llm_client": MagicMock(),
        }):
            import realtime_subtitle.web.web_server as module

            module.web.json_response.side_effect = lambda data, status=200: (data, status)

            empty_server = self._create_server(module, {})
            get_req = AsyncMock()
            get_req.remote = "127.0.0.1"
            data, status = await empty_server.learning_languages_get_handler(get_req)
            assert status == 200
            assert data == {
                "status": "ok",
                "languages": {"ja": True, "zh": True, "ko": True},
                "initialized": False,
            }

            stored_server = self._create_server(module, {
                "learningLanguages.v1": json.dumps({"ja": True, "zh": False, "ko": True}),
            })
            stored_server.broadcast_to_clients = AsyncMock()
            data, status = await stored_server.learning_languages_get_handler(get_req)
            assert status == 200
            assert data == {
                "status": "ok",
                "languages": {"ja": True, "zh": False, "ko": True},
                "initialized": True,
            }

            # Non-loopback POST rejected
            remote_req = AsyncMock()
            remote_req.remote = "10.0.0.8"
            remote_req.path = "/learning-languages"
            data, status = await stored_server.learning_languages_post_handler(remote_req)
            assert status == 403

            # Invalid JSON returns 400
            bad_req = AsyncMock()
            bad_req.remote = "127.0.0.1"
            bad_req.json.side_effect = ValueError("bad json")
            data, status = await stored_server.learning_languages_post_handler(bad_req)
            assert status == 400

            # Valid POST updates state, ignores non-bool/unknown keys, and broadcasts
            post_req = AsyncMock()
            post_req.remote = "127.0.0.1"
            post_req.json.return_value = {
                "languages": {"ja": False, "zh": True, "ko": "nope", "en": True},
            }
            data, status = await stored_server.learning_languages_post_handler(post_req)
            assert status == 200
            expected = {"ja": False, "zh": True, "ko": True}
            assert data == {"status": "ok", "languages": expected, "initialized": True}
            assert stored_server.get_learning_languages_state() == expected
            assert stored_server.is_learning_language_enabled("ja") is False
            assert stored_server.is_learning_language_enabled("zh") is True
            stored_server.broadcast_to_clients.assert_awaited_once_with({
                "type": "learning_languages",
                "languages": expected,
                "initialized": True,
            })

    @async_test
    async def test_furigana_handler_forwards_lang_and_supports_one_arg_evaluator(self):
        with patch_modules({
            "aiohttp": MagicMock(),
            "aiohttp.web": MagicMock(),
            "realtime_subtitle.core.config": MagicMock(),
            "realtime_subtitle.llm.llm_client": MagicMock(),
        }):
            import realtime_subtitle.web.web_server as module

            module.web.json_response.side_effect = lambda data, status=200: (data, status)
            server = self._create_server(module, {})

            two_arg_calls = []

            def eval_two(text, lang):
                two_arg_calls.append((text, lang))
                return {"ready": True, "pairs": [[text, f"{lang}-ok"]]}

            server.set_furigana_evaluator(eval_two)

            req_zh = AsyncMock()
            req_zh.remote = "127.0.0.1"
            req_zh.json.return_value = {"text": "你好", "lang": "zh"}
            data, status = await server.furigana_handler(req_zh)
            assert status == 200
            assert data == {"ready": True, "pairs": [["你好", "zh-ok"]]}

            req_default = AsyncMock()
            req_default.remote = "127.0.0.1"
            req_default.json.return_value = {"text": "猫"}
            data, status = await server.furigana_handler(req_default)
            assert status == 200
            assert two_arg_calls == [("你好", "zh"), ("猫", "ja")]

            # Legacy 1-arg evaluator still works
            one_arg_calls = []

            def eval_one(text):
                one_arg_calls.append(text)
                return {"ready": True, "pairs": [[text, "legacy"]]}

            server.set_furigana_evaluator(eval_one)
            data, status = await server.furigana_handler(req_zh)
            assert status == 200
            assert data == {"ready": True, "pairs": [["你好", "legacy"]]}
            assert one_arg_calls == ["你好"]
