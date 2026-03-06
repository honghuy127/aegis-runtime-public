from llm.html_compaction import semantic_html_chunks_for_prompt


def test_semantic_html_chunks_for_prompt_prioritizes_route_date_price_blocks():
    html = """
    <html><body>
      <main>
        <div data-testid="route-box">
          <input aria-label="Where from" value="FUK" />
          <input aria-label="Where to" value="HND" />
          <button data-testid="depart-btn" aria-label="Departure">2026/05/02</button>
          <button data-testid="return-btn" aria-label="Return">2026/06/08</button>
        </div>
        <section>
          <h2>Search results</h2>
          <div>Cheapest</div>
          <div>¥24,900</div>
        </section>
      </main>
    </body></html>
    """
    chunks = semantic_html_chunks_for_prompt(
        html,
        max_chunks=2,
        chunk_chars=1200,
        max_total_chars=2400,
    )
    assert isinstance(chunks, list)
    assert len(chunks) == 2
    assert any("Where from" in str(chunk.get("html", "")) for chunk in chunks)
    assert any("¥24,900" in str(chunk.get("html", "")) for chunk in chunks)


def test_semantic_html_chunks_for_prompt_returns_empty_for_empty_html():
    assert semantic_html_chunks_for_prompt("") == []
