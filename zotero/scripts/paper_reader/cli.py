"""Reader commands extend the existing CLI without changing old commands."""
import json
from pathlib import Path
import zipfile
from . import storage as s, jobs, review, evidence, matrix, native, quality, discovery


def register(subcommands, api):
    doctor = subcommands.add_parser("reader-doctor", help="Check/prepare optional local PDF, font and OCR components")
    doctor.add_argument("--pdf", action="store_true")
    doctor.add_argument("--ocr", action="store_true")
    doctor.set_defaults(func=lambda args: dispatch("doctor", args, api))
    config = subcommands.add_parser("reader-config", help="Inspect/save reading defaults; omitted outputs trigger a task choice")
    config.add_argument("--input", help="JSON settings to merge and validate")
    config.set_defaults(func=lambda args: dispatch("config", args, api))
    profile = subcommands.add_parser("research-profile", help="List/show/save a general research profile")
    profile.add_argument("name", nargs="?")
    profile.add_argument("--input", help="JSON with question, keywords, priorities and open_questions")
    profile.set_defaults(func=lambda args: dispatch("profile", args, api))

    reader = subcommands.add_parser("read", help="Resumable paper reading with DSH's current model")
    commands = reader.add_subparsers(dest="reading_action", required=True)
    prepare = commands.add_parser("prepare", help="Resolve Zotero selection and cache PDF geometry; writes no Zotero items")
    api.add_selection_args(prepare)
    prepare.add_argument("--selected", action="store_true", help="Read selected items through the authorized companion")
    prepare.add_argument("--attachment", help="Choose a PDF attachment key when a paper has multiple PDFs")
    prepare.add_argument("--ocr", choices=["auto", "off"], default="auto")
    for key, choices in s.CHOICES.items():
        prepare.add_argument("--" + key.replace("_", "-"), choices=sorted(choices))
    prepare.add_argument("--outputs", nargs="+", choices=sorted(s.OUTPUTS))
    prepare.add_argument("--profile")
    prepare.add_argument("--max-workers", type=int)
    prepare.set_defaults(func=lambda args: dispatch("prepare", args, api))
    for name in ("status", "accept", "revise", "history", "audit", "outline", "annotations", "annotate", "export", "publish"):
        command = commands.add_parser(name)
        command.add_argument("job")
        if name in {"accept", "revise"}:
            command.add_argument("--input", required=True)
        if name == "accept":
            command.add_argument("--strict", action="store_true", help="Require number/glossary audit to pass")
        if name == "revise":
            command.add_argument("--expected-revision", required=True)
            command.add_argument("--reason", required=True)
        if name == "export":
            command.add_argument("--out", required=True)
            command.add_argument("--partial", action="store_true", help="Export explicitly incomplete Markdown only")
        if name in {"publish", "annotate"}:
            command.add_argument("--yes", action="store_true")
        command.set_defaults(func=lambda args, action=name: dispatch(action, args, api))
    listing = commands.add_parser("list")
    listing.set_defaults(func=lambda args: dispatch("list", args, api))
    ask = commands.add_parser("ask", help="Retrieve a small, scoped passage context for the current host model")
    ask.add_argument("--jobs", nargs="+", required=True)
    ask.add_argument("--question", required=True)
    ask.add_argument("--query", help="Optional English keywords for cross-language lexical retrieval")
    ask.add_argument("--profile", default="")
    ask.add_argument("--max-chars", type=int, default=10000)
    ask.add_argument("--out", required=True)
    ask.set_defaults(func=lambda args: dispatch("ask", args, api))
    answer = commands.add_parser("answer")
    answer.add_argument("--context", required=True)
    answer.add_argument("--input", required=True)
    answer.add_argument("--out", required=True)
    answer.set_defaults(func=lambda args: dispatch("answer", args, api))

    corpus = subcommands.add_parser("review", help="Reuse accepted paper evidence for comparison and gap-directed search")
    actions = corpus.add_subparsers(dest="review_action", required=True)
    prepare_review = actions.add_parser("prepare")
    prepare_review.add_argument("--jobs", nargs="+", required=True)
    prepare_review.add_argument("--profile", default="")
    prepare_review.add_argument("--out", required=True)
    prepare_review.set_defaults(func=lambda args: dispatch("review-prepare", args, api))
    render_review = actions.add_parser("render")
    render_review.add_argument("--corpus", required=True)
    render_review.add_argument("--input", required=True)
    render_review.add_argument("--out", required=True)
    render_review.set_defaults(func=lambda args: dispatch("review-render", args, api))
    search = actions.add_parser("search")
    search.add_argument("--queries", nargs="+", required=True)
    search.add_argument("--profile", default="")
    search.add_argument("--limit", type=int, default=12)
    search.add_argument("--out")
    search.set_defaults(func=lambda args: dispatch("review-search", args, api))
    discover = actions.add_parser("discover", help="Bounded reference/citation chaining; metadata candidates only")
    discover.add_argument("--jobs", nargs="+", required=True)
    discover.add_argument("--direction", choices=["references", "citations", "both"], default="both")
    discover.add_argument("--limit", type=int, default=12)
    discover.add_argument("--out")
    discover.set_defaults(func=lambda args: dispatch("discover", args, api))
    table = actions.add_parser("matrix", help="Custom evidence comparison columns")
    table_actions = table.add_subparsers(dest="matrix_action", required=True)
    table_prepare = table_actions.add_parser("prepare")
    table_prepare.add_argument("--jobs", nargs="+", required=True)
    table_prepare.add_argument("--columns", nargs="+")
    table_prepare.add_argument("--profile", default="")
    table_prepare.add_argument("--out", required=True)
    table_prepare.set_defaults(func=lambda args: dispatch("matrix-prepare", args, api))
    table_render = table_actions.add_parser("render")
    table_render.add_argument("--context", required=True)
    table_render.add_argument("--input", required=True)
    table_render.add_argument("--out", required=True, help="Output filename prefix for Markdown, CSV and evidence JSON")
    table_render.set_defaults(func=lambda args: dispatch("matrix-render", args, api))
    bridge = subcommands.add_parser("bridge", help="Package the small companion for one-time installation in Zotero")
    bridge.add_argument("--out", required=True)
    bridge.set_defaults(func=lambda args: dispatch("bridge", args, api))


def dispatch(action, args, api):
    try:
        result = run(action, args, api)
        api.dump_json(result)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"Reader: {error}")


def run(action, args, api):
    if action == "doctor":
        from . import pdf
        checked = ["stdlib"]
        if args.pdf or args.ocr:
            pdf.dependencies()
            pdf.font_name()
            checked += ["pdf", "embedded-cjk-font"]
        if args.ocr:
            import importlib.util
            if importlib.util.find_spec("rapidocr_onnxruntime") is None:
                pdf.component("ocr")
            checked.append("ocr")
        return {"checked": checked, "model_service": "host-current-model; no separate inference configuration"}
    if action == "config":
        settings = s.settings(s.read(args.input) if args.input else {})
        if args.input:
            s.write(s.root() / "reader-settings.json", settings)
        return settings
    if action == "profile":
        directory = s.root() / "reader-profiles"
        if not args.name:
            if args.input:
                raise ValueError("Name the profile to save")
            return {"profiles": [p.stem for p in sorted(directory.glob("*.json"))]}
        if args.input:
            data = s.read(args.input)
            if not isinstance(data, dict) or not isinstance(data.get("question"), str) or not data["question"].strip():
                raise ValueError("Profile needs a nonempty question")
            for field in ("keywords", "priorities", "open_questions"):
                if not isinstance(data.get(field, []), list) or not all(isinstance(v, str) and v.strip() for v in data.get(field, [])):
                    raise ValueError(f"{field} must be a list of nonempty strings")
            quality.profile_fields(data)
            s.write(directory / (s.identifier(args.name) + ".json"), data)
        return s.profile(args.name)
    if action == "prepare":
        if args.selected and any((args.keys, args.collection, args.tag, args.query, args.all)):
            raise ValueError("Choose --selected or an explicit selection, not both")
        return jobs.prepare(api, args)
    if action == "status":
        return jobs.inspect(args.job)
    if action == "accept":
        return jobs.accept(args.job, s.read(args.input), strict=args.strict)
    if action == "revise":
        return jobs.revise(args.job, s.read(args.input), args.expected_revision, args.reason)
    if action == "history":
        return jobs.history(args.job)
    if action == "audit":
        return {"issues": jobs.inspect(args.job)["translation_audit"], "note": "Deterministic checks identify review candidates, not scientific correctness"}
    if action == "outline":
        return jobs.outline(args.job)
    if action == "annotations":
        return native.collect(api, args.job)
    if action == "annotate":
        return native.publish(api, args.job, yes=args.yes)
    if action == "ask":
        return evidence.prepare_question(args.jobs, args.question, args.out, query=args.query, profile_name=args.profile, max_chars=args.max_chars)
    if action == "answer":
        return evidence.answer(s.read(args.context), s.read(args.input), args.out)
    if action == "export":
        return jobs.export(args.job, args.out, partial=args.partial)
    if action == "publish":
        api.ensure_yes(args, "create a new reading note in Zotero")
        return jobs.publish_note(api, args.job)
    if action == "list":
        return [{"id": p.parent.name, "title": s.read(p)["title"], "settings": s.read(p)["settings"]}
                for p in sorted((s.root() / "reader-jobs").glob("*/manifest.json"))]
    if action == "review-prepare":
        return review.prepare(args.jobs, args.profile, args.out)
    if action == "review-render":
        corpus = s.read(args.corpus)
        result = review.render(corpus, s.read(args.input), args.out)
        # Missing evidence always triggers a fresh recommendation search.
        result["supplement"] = review.search(api, result["search_queries"], corpus["profile"])
        s.write(Path(args.out).with_suffix(".recommendations.json"), result["supplement"])
        with Path(args.out).open("a", encoding="utf-8") as stream:
            stream.write("\n## 自动检索的补充候选（尚待全文验证）\n\n")
            for row in result["supplement"]["recommendations"]:
                stream.write(f"- {row['title']} · {row['recommendation_reason']} · {row['evidence_level']}\n")
            stream.write("\n" + result["supplement"]["network_or_coverage_warning"] + "\n")
        return result
    if action == "review-search":
        if not 1 <= args.limit <= 100:
            raise ValueError("limit must be from 1 to 100")
        result = review.search(api, args.queries, args.profile, args.limit)
        if args.out:
            if Path(args.out).exists():
                raise ValueError("Search output already exists")
            s.write(args.out, result)
        return result
    if action == "discover":
        result = discovery.discover(api, args.jobs, args.direction, args.limit)
        if args.out:
            evidence.write_new(args.out, result)
        return result
    if action == "matrix-prepare":
        return matrix.prepare(args.jobs, args.profile, args.columns, args.out)
    if action == "matrix-render":
        return matrix.render(s.read(args.context), s.read(args.input), args.out)
    if action == "bridge":
        directory = Path(__file__).resolve().parents[2] / "bridge"
        target = Path(args.out).expanduser().resolve()
        if target.exists():
            raise ValueError("Bridge output already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in ("manifest.json", "bootstrap.js"):
                archive.write(directory / name, name)
        return {"xpi": str(target), "install": "Zotero → Tools → Plugins → Install Plugin From File; then authorize local API access"}
    raise ValueError(f"Unknown action: {action}")
