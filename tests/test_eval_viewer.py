"""tools/eval/viewer.py — markdown mini-renderer + data assembly."""
import json

from tools.eval import viewer


def test_md_to_html_basics():
    html_out = viewer.md_to_html(
        "## Head\n\nPara with **bold** and \\$44/MWh.\n\n- one\n- two\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n<!-- image_id:7 -->")
    assert "<h3>Head</h3>" in html_out
    assert "<strong>bold</strong>" in html_out
    assert "$44/MWh" in html_out and "\\$" not in html_out
    assert html_out.count("<li>") == 2
    assert "<table>" in html_out and "<th>a</th>" in html_out
    assert "image #7" in html_out


def test_md_escapes_html():
    assert "<script>" not in viewer.md_to_html("hello <script>alert(1)</script>")


def _run(tmp_path, name, one_line, body, judge=None):
    d = tmp_path / name
    d.mkdir()
    (d / "iso_item_admin_remarks.json").write_text(json.dumps({
        "case_id": "iso_item_admin_remarks", "kind": "iso_item",
        "outputs": [{"entity_type": "agenda_item", "entity_id": 865,
                     "one_line": one_line, "detailed": body,
                     "model_id": "m"}],
    }), encoding="utf-8")
    if judge:
        (d / "scores_judge.json").write_text(json.dumps(
            {"judge": {"iso_item_admin_remarks": judge}}), encoding="utf-8")
    return viewer._load_run(d)


def test_build_matches_entities_and_unescapes(tmp_path):
    a = _run(tmp_path, "a", "TLDR \\$5", "body A",
             judge={"overall": 4, "rationale": "costs \\$5", "defects": ["\\$9 wrong"]})
    b = _run(tmp_path, "b", None, "body B")
    page, key = viewer.build(a, b, blind=False, title="t")
    assert "body A" in page and "body B" in page
    assert "TLDR $5" in page and "costs $5" in page and "$9 wrong" in page
    assert "\\\\$" not in page
    assert key["assignment"]["iso_item_admin_remarks"]["left"] == "a"


def test_blind_hides_metadata(tmp_path):
    a = _run(tmp_path, "a", "one", "body A", judge={"overall": 4})
    b = _run(tmp_path, "b", "two", "body B")
    page, key = viewer.build(a, b, blind=True, title="t")
    data = json.loads(page.split("const DATA = ", 1)[1].split(";\n", 1)[0])
    ent = data["cases"][0]["entities"]["agenda_item:865"]
    assert ent["left"]["judge"] is None and ent["left"]["model"] is None
    assert data["labelA"] == "Side 1"
    assert key["blind"] is True
