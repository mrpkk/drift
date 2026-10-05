# dev.to — статья

**Заголовок:** Your agent's spending mandate is not being audience-checked

**Теги:** `ai` `agents` `security` `mcp` `opensource` `payments` `x402`

**Обложка:** `assets/vk-cover.png` (1200×675)

**Тело** — файл `telegram-announce-en.md`, расширенный абзацем:

Why the gap is real: in Google's AP2 Python SDK the audience check is skipped for
key-bound hops, so a token minted for another audience is accepted. drift re-checks every
action against the signed mandate before it happens — amount, payee, audience, currency,
window and intent — with deny as the default.

The interesting consequence is not the bug itself. It is that an entire class of agent
guardrails assumes the payment layer already checked who the payment is for. It didn't,
and in the one language with a working verifier.

What I would like argued with: whether checking `aud` on every hop is the right call, or
whether `kb+sd-jwt+kb` should simply be classified as terminal in the hop chain. Both are
one-line fixes; neither is mine to make.
