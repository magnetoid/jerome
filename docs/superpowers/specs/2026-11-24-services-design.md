# More translation and proofreading services

Approved by the owner in chat on 2026-11-24.

## Why

The owner wants well-known services beside Claude for proofreading and translation, including open-weight models. The aims are choice of quality and price, and no dependence on a single vendor.

## What

1. **Model providers.** The proofreading model (`models.ai`) and the translation model (`models.translate`) can each use one of these providers:
   - `anthropic`, as today;
   - `openai`, at `https://api.openai.com/v1`;
   - `gemini`, at `https://generativelanguage.googleapis.com/v1beta/openai`;
   - `mistral`, at `https://api.mistral.ai/v1`;
   - `openrouter`, at `https://openrouter.ai/api/v1`, which gives open-weight models (Llama, Qwen, DeepSeek, Mistral, Gemma) with one key;
   - `openai_compat`, the "own server or other" option (Ollama, vLLM, anything OpenAI-compatible). It has a `base_url`, as today.
2. **Provider keys.** Each provider has its own secret, set on the Settings page:
   - `openai_api_key`;
   - `gemini_api_key`;
   - `mistral_api_key`;
   - `openrouter_api_key`;
   - `openai_compat_api_key`, which already exists.
3. **Model names** are typed by the owner. The web suggests a few well-known names per provider, as a list to pick from (`datalist`), and does not restrict them.
4. **Prices.** Models with no built-in price get one in the Prices setting, as today. An estimate is refused until the price is set.
5. **DeepL** is a third choice for translation only (`models.translate`):
   - Provider `deepl`, model `deepl`, key `deepl_api_key`.
   - A key ending `:fx` uses `api-free.deepl.com`; any other key uses `api.deepl.com`.
   - Each item's tagged text is sent with XML tag handling. `⟦N⟧` placeholders travel as `<x n="N"/>`. Ignore-list words travel inside `<keep>`, which DeepL leaves untranslated.
   - The reply goes through the same decode and checks as a model's reply. Failed items are retried and then flagged, as with a model.
   - Billing is per character: the usage records source characters as input tokens. The Prices entry for `deepl` holds the price per million characters in `input`.
   - **What DeepL loses:** house style such as the quote style. Proofreading after translation is expected to catch it.
6. **LanguageTool Premium.**
   - New settings: `languagetool_username` (plain) and `languagetool_api_key` (secret). When both are set, every check sends them, and the owner points `languagetool_url` at `https://api.languagetoolplus.com`.
   - A 429 reply is retried after `Retry-After` (at most 3 times, at most 60 s each).
7. **Not added:**
   - Grammarly, which has no public API;
   - DeepL Write, which rewrites text instead of listing corrections.

## Constraints

- No API keys in the repo, in compose or in logs. Keys are entered only on the Settings page or through the environment.
- The default model stays `claude-opus-5-5`.
- A book marked local-only can only use a local provider (`openai_compat` on a local host).
- Tests never call real services; they use respx.
