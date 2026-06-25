"""
inspect_tokens.py  —  BONUS for Phase 1 ("How LLMs work").

LLMs don't read letters or words — they read "tokens" (chunks of text turned
into numbers). This little script lets you SEE that happen, so you can explain
tokenization to anyone.

Run it:
    python inspect_tokens.py
"""

import tiktoken

# gpt-4o-mini uses this tokenizer.
encoder = tiktoken.get_encoding("o200k_base")

text = input("Type some text to tokenize: ") or "Cloudastra helps small businesses use AI."

# Turn the text into token IDs (the numbers the model actually sees).
token_ids = encoder.encode(text)

print(f"\nYour text: {text!r}")
print(f"Number of tokens: {len(token_ids)}")
print(f"Token IDs (the numbers the AI reads): {token_ids}\n")

# Show what each token ID maps back to, so you can see the 'pieces'.
print("How your text was split into tokens:")
for tid in token_ids:
    piece = encoder.decode([tid])
    print(f"  {tid:>7}  ->  {piece!r}")

print("\nTakeaway: the AI never sees words — it sees these numbered pieces (tokens).")
