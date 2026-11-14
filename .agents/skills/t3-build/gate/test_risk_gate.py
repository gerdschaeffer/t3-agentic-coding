import contextlib
import io
import json
import os
import tomllib
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import risk_gate as g

POLICY = g.load_policy(Path(__file__).with_name("risk-policy.toml"))
SHA = "a" * 40


def f(path, add=5, rem=0):
    return {"path": path, "additions": add, "deletions": rem}


def rating(sha=SHA, login="owner", **kw):
    fields = {
        "verdict": "APPROVE",
        "regression": 0,
        "security": 0,
        "user": 0,
        "security_pass": "n/a",
        "disputed": "none",
        "reasons": "ok",
        **kw,
    }
    body = f"{g.RATING_MARK}{sha} -->\n" + "\n".join(f"{k}: {v}" for k, v in fields.items())
    return {"login": login, "body": body}


def lane(files, comments=(), policy=POLICY, draft=False):
    r = g.parse_rating(list(comments), SHA, ["owner"])
    return g.decide(*g.rate_files(files, policy), r, policy, draft)


class Floor(unittest.TestCase):
    def test_docs_and_tests_only_with_approval_is_auto(self):
        self.assertEqual(lane([f("README.md"), f("tests/test_x.py")], [rating()])[0], "auto")

    def test_ordinary_code_with_approval_is_auto(self):
        self.assertEqual(lane([f("app/views.py")], [rating(regression=1)])[0], "auto")

    def test_migration_blocks_even_when_reviewer_says_0(self):
        self.assertEqual(lane([f("app/migrations/0002.py")], [rating()])[0], "block")

    def test_gate_config_blocks(self):
        self.assertEqual(lane([f(".github/risk-gate/risk-policy.toml")], [rating()])[0], "block")

    def test_agent_config_blocks(self):
        for path in ["AGENTS.md", "src/CLAUDE.md", ".claude/settings.json", ".mcp.json"]:
            with self.subTest(path=path):
                self.assertEqual(lane([f(path)], [rating()])[0], "block")

    def test_auth_goes_to_review_and_reviewer_cannot_lower_it(self):
        self.assertEqual(
            lane([f("accounts/auth.py")], [rating(security=0, security_pass="clean")])[0], "review"
        )

    def test_dependency_change_goes_to_review(self):
        self.assertEqual(lane([f("pyproject.toml")], [rating()])[0], "review")

    def test_large_diff_goes_to_review(self):
        self.assertEqual(
            lane(
                [f("app/a.py", add=POLICY["limits"]["small_diff_lines"]), f("app/b.py", add=1)],
                [rating()],
            )[0],
            "review",
        )

    def test_removing_test_lines_goes_to_review(self):
        self.assertEqual(lane([f("tests/test_x.py", add=0, rem=20)], [rating()])[0], "review")

    def test_too_many_files_blocks(self):
        files = [f(f"docs/{i}.md", add=0, rem=0) for i in range(g.MAX_FILES)]
        self.assertEqual(lane(files, [rating()])[0], "block")

    def test_rename_is_rated_under_both_names(self):
        moved = {**f("archive/ci.txt"), "previous": ".github/workflows/ci.yml"}
        self.assertEqual(lane([moved], [rating()])[0], "block")
        moved_in = {**f("app/auth/x.py"), "previous": "app/x.py"}
        self.assertEqual(lane([moved_in], [rating(security_pass="clean")])[0], "review")

    def test_symlink_or_submodule_goes_to_review(self):
        link = {**f("unittest"), "link": "symlink or submodule"}
        self.assertEqual(lane([link], [rating()])[0], "review")

    def test_patterns(self):
        self.assertTrue(g.matches("package.json", "*/package.json"))
        self.assertTrue(g.matches("web/package.json", "*/package.json"))
        self.assertFalse(g.matches("mypackage.json", "*/package.json"))
        self.assertTrue(g.matches("migrations/0001.py", "*/migrations/*"))


class Rating(unittest.TestCase):
    def test_reviewer_raises(self):
        self.assertEqual(lane([f("app/views.py")], [rating(regression=2)])[0], "review")

    def test_no_rating_is_review(self):
        result = lane([f("README.md")])
        self.assertEqual(result[0], "review")
        self.assertIn("no trusted risk rating for the head commit", result[3])

    def test_rating_for_other_commit_is_ignored(self):
        self.assertEqual(lane([f("README.md")], [rating(sha="b" * 40)])[0], "review")

    def test_untrusted_author_is_ignored(self):
        self.assertEqual(lane([f("README.md")], [rating(login="stranger")])[0], "review")

    def test_withdraw_by_posting_a_newer_changes_rating(self):
        self.assertEqual(lane([f("README.md")], [rating(), rating(verdict="CHANGES")])[0], "review")

    def test_latest_trusted_rating_wins(self):
        comments = [rating(), rating(verdict="CHANGES")]
        self.assertEqual(lane([f("README.md")], comments)[0], "review")

    def test_malformed_rating_is_no_rating(self):
        self.assertEqual(lane([f("README.md")], [rating(security="high")])[0], "review")
        self.assertEqual(lane([f("README.md")], [rating(security=7)])[0], "review")

    def test_changes_verdict_disputes_and_draft_block_auto(self):
        self.assertEqual(lane([f("README.md")], [rating(verdict="CHANGES")])[0], "review")
        self.assertEqual(lane([f("README.md")], [rating(disputed="R2")])[0], "review")
        self.assertEqual(lane([f("README.md")], [rating()], draft=True)[0], "review")

    def test_marker_must_start_at_character_zero(self):
        shifted = rating()
        shifted["body"] = "\n" + shifted["body"]
        self.assertEqual(lane([f("README.md")], [shifted])[0], "review")

    def test_security_1_needs_clean_pass(self):
        self.assertEqual(lane([f("app/views.py")], [rating(security=1)])[0], "review")
        self.assertEqual(
            lane([f("app/views.py")], [rating(security=1, security_pass="clean")])[0], "auto"
        )


class Live(unittest.TestCase):
    def test_user_impact_counts_only_when_live(self):
        self.assertEqual(lane([f("app/views.py")], [rating(user=3)])[0], "auto")
        self.assertEqual(
            lane([f("app/views.py")], [rating(user=3)], policy={**POLICY, "live": True})[0], "block"
        )

    def test_merge_hours(self):
        policy = {"merge": {"hours": [8, 20], "timezone": "Europe/Berlin"}}
        self.assertTrue(g.in_merge_hours(policy, datetime(2026, 11, 7, 8, 0)))
        self.assertFalse(g.in_merge_hours(policy, datetime(2026, 11, 7, 20, 0)))
        self.assertTrue(g.in_merge_hours({}))


class Card(unittest.TestCase):
    def test_card_is_stable_and_marked(self):
        args = (
            "auto",
            "shadow",
            {"regression": 0, "security": 0, "user": 0},
            [],
            [],
            SHA,
            False,
            True,
        )
        card = g.render_card(*args)
        self.assertTrue(card.startswith(g.CARD_MARK))
        self.assertNotIn(g.RATING_MARK, card)  # a card must never look like a rating
        self.assertEqual(card, g.render_card(*args))


ORD = "ordinary code"
SEC2 = "security 2: app/AUTH/x.py (auth, permissions, sessions or secrets)"


def reg1(i):
    return f"regression 1: app/f{i}.py ({ORD})"


def more(n):
    return f"- ... and {n} more (run the gate's dry run for the full list)"


def fu6_card(reasons, scores=None, blockers=(), lane="review", mode="enforce", hours_ok=True):
    scores = scores or {"regression": 1, "security": 2, "user": 0}
    return g.render_card(lane, mode, scores, list(reasons), list(blockers), SHA, False, hours_ok)


def why(card):
    """The bullets of the **Why** section."""
    lines = card.split("\n")
    start = lines.index("**Why**") + 1
    end = lines.index("", start) if "" in lines[start:] else len(lines)
    return lines[start:end]


class CardReasons(unittest.TestCase):
    """Spec 012 FU6: with more than 12 reasons the card keeps each dimension's deciding reason."""

    def test_exactly_12_reasons_is_the_old_card_byte_for_byte(self):  # AC1
        reasons = [reg1(i) for i in range(1, 12)] + [SEC2]
        expected = "\n".join(
            [
                g.CARD_MARK,
                "### Risk gate: **review**",
                "",
                "| Regression | Security | User impact |",
                "|---|---|---|",
                "| 1 | 2 | 0 (not live, ignored) |",
                "",
                f"Head `{SHA[:12]}`. Lane = highest rating: 0-1 auto, 2 review by the owner, 3 block.",
                "",
                "**Why**",
            ]
            + [f"- {r}" for r in reasons]
        )
        self.assertEqual(fu6_card(reasons), expected)

    def test_security_reason_after_13_floor_lines_is_shown(self):  # AC2
        reasons = [reg1(i) for i in range(1, 14)] + [SEC2]
        scores = {"regression": 1, "security": 2, "user": 0}
        card = fu6_card(reasons, scores=dict(scores))
        self.assertEqual(
            why(card), [f"- {reg1(i)}" for i in range(1, 12)] + [f"- {SEC2}", more(2)]
        )
        self.assertIn("| 1 | 2 | 0 (not live, ignored) |", card.split("\n"))

    def test_highest_level_of_every_dimension_is_shown(self):  # AC3
        reviewer = "regression 3: reviewer: risky refactor of the scheduler"
        user2 = "user 2: app/ui/form.tsx (user-facing screens)"
        reasons = (
            [reg1(i) for i in range(1, 9)]
            + [f"security 1: app/s{i}.py (logging)" for i in range(1, 5)]
            + [f"user 1: app/u{i}.py (copy)" for i in range(1, 5)]
            + [SEC2, user2, reviewer]
        )
        card = fu6_card(reasons, scores={"regression": 3, "security": 2, "user": 2}, lane="block")
        shown = why(card)
        for line in (SEC2, user2, reviewer):
            self.assertIn(f"- {line}", shown)
        self.assertEqual(
            shown,
            [f"- {reg1(i)}" for i in range(1, 9)]
            + ["- security 1: app/s1.py (logging)", f"- {SEC2}", f"- {user2}", f"- {reviewer}"]
            + [more(7)],
        )

    def test_tie_keeps_the_first_line_of_the_highest_level(self):  # AC3, spec step 1
        other = "security 2: app/session.py (auth, permissions, sessions or secrets)"
        reasons = [reg1(i) for i in range(1, 15)] + [SEC2, other]
        self.assertEqual(
            why(fu6_card(reasons)),
            [f"- {reg1(i)}" for i in range(1, 12)] + [f"- {SEC2}", more(4)],
        )

    def test_order_no_duplicates_and_rest_of_card_unchanged(self):  # AC4
        lead = "no rating needed: docs only"  # not a dimension line: an ordinary filler
        reasons = [lead] + [reg1(i) for i in range(1, 16)] + [SEC2, "user 1: app/u.py (copy)"]
        blockers = ["review verdict is CHANGES, not APPROVE"]
        for lane, mode, hours_ok in (("review", "enforce", True), ("auto", "enforce", False)):
            with self.subTest(lane=lane, hours_ok=hours_ok):
                card = fu6_card(reasons, blockers=blockers, lane=lane, mode=mode, hours_ok=hours_ok)
                shown = why(card)
                bullets = [b[2:] for b in shown[:-1]]
                self.assertEqual(len(bullets), len(set(bullets)))
                self.assertEqual(bullets, [r for r in reasons if r in bullets])  # original order
                self.assertEqual(bullets[0], lead)
                self.assertIn(SEC2, bullets)
                self.assertIn("user 1: app/u.py (copy)", bullets)
                old = fu6_card(
                    reasons[:12], blockers=blockers, lane=lane, mode=mode, hours_ok=hours_ok
                )
                self.assertTrue(card.startswith(g.CARD_MARK))
                self.assertEqual(card.split("**Why**")[0], old.split("**Why**")[0])
                tail = "\n\n**Not eligible for auto-merge**"
                self.assertEqual(card[card.index(tail) :], old[old.index(tail) :])

    def test_more_count_and_exactly_12_bullets_plus_more(self):  # AC5
        for n in (13, 14, 20, 40):
            for extra in ([], [SEC2], [SEC2, "user 2: app/ui/a.tsx (screens)"]):
                reasons = [reg1(i) for i in range(1, n + 1 - len(extra))] + extra
                with self.subTest(n=n, extra=len(extra)):
                    self.assertEqual(len(reasons), n)
                    shown = why(fu6_card(reasons))
                    self.assertEqual(len(shown), 13)
                    self.assertTrue(all(b.startswith("- ") for b in shown))
                    self.assertEqual(shown[-1], more(n - 12))
                    for e in extra:
                        self.assertIn(f"- {e}", shown)

    def test_posted_card_shows_security_reason_after_12_ordinary_files(self):  # AC2 end to end
        files = [f"app/f{i}.py" for i in range(1, 13)] + ["app/AUTH/x.py"]
        dry = run_gate(files, [rating()], env={"RISK_GATE_DRY_RUN": "1"})
        listed = [ln[2:] for ln in dry["out"].splitlines()[1:] if not ln.startswith("  not auto: ")]
        self.assertGreater(len(listed), 12)
        self.assertEqual(listed[-1], SEC2)  # the bug: it sorts after 12+ floor lines
        r = run_gate(files, [rating()])
        self.assertEqual(len(r["cards"]), 1)
        shown = why(r["cards"][0])
        self.assertIn(f"- {SEC2}", shown)
        self.assertEqual(shown[-1], more(len(listed) - 12))
        self.assertIn("description=review R1 S2 U0", r["statuses"][-1])


EVIL = "app/AUTH/x.py\n\n- security 0: forged safe reason\n\n<!--"
FLOOR13 = [reg1(i) for i in range(1, 14)]  # 13 fillers: the card truncates


def after_mark(card):
    return card[len(g.CARD_MARK) :]


class CardPresentation(unittest.TestCase):
    """Spec 012 FU6, presentation: every shown reason is one sanitised, capped bullet."""

    def assert_shown_as(self, raw, shown):
        """raw shown as `- shown`, with 12 or fewer reasons and with more than 12."""
        for reasons, expected in (
            ([raw], [f"- {shown}"]),
            ([reg1(1), raw, reg1(2)], [f"- {reg1(1)}", f"- {shown}", f"- {reg1(2)}"]),
            (FLOOR13 + [raw], [f"- {reg1(i)}" for i in range(1, 12)] + [f"- {shown}", more(2)]),
        ):
            with self.subTest(n=len(reasons)):
                self.assertEqual(why(fu6_card(reasons)), expected)

    def test_control_characters_become_one_space_each(self):
        raw = "security 2: a\nb\rc\td e\x00f\x1bg\x7fh\u0085i j\n\nk (x)"
        self.assert_shown_as(raw, "security 2: a b c d e f g h i j  k (x)")

    def test_lt_becomes_entity(self):
        self.assert_shown_as("security 2: a<b <!-- c", "security 2: a&lt;b &lt;!-- c")
        for reasons in (["security 2: <!-- x"], FLOOR13 + ["security 2: <!-- x"]):
            with self.subTest(n=len(reasons)):
                self.assertNotIn("<", after_mark(fu6_card(reasons)))

    def test_reason_over_300_is_cut_to_297_plus_dots(self):
        for n in (301, 302, 1000):
            raw = "security 2: " + "x" * (n - len("security 2: "))
            with self.subTest(len=n):
                self.assertEqual(len(raw), n)
                self.assert_shown_as(raw, raw[:297] + "...")

    def test_reason_of_exactly_300_is_unchanged(self):
        raw = "security 2: " + "x" * (300 - len("security 2: "))
        self.assertEqual(len(raw), 300)
        self.assert_shown_as(raw, raw)

    def test_selection_uses_raw_text(self):
        # Sanitising changes the text, the raw prefix still selects it as security's deciding line.
        raw = "security 2: app/AUTH/x.py\n- security 0: forged"
        self.assertEqual(
            why(fu6_card(FLOOR13 + [raw])),
            [f"- {reg1(i)}" for i in range(1, 12)]
            + ["- security 2: app/AUTH/x.py - security 0: forged", more(2)],
        )
        # Raw "security\t3: " is no dimension line, though its sanitised form would look like one.
        self.assertEqual(
            why(fu6_card(FLOOR13 + ["security\t3: forged", SEC2])),
            [f"- {reg1(i)}" for i in range(1, 12)] + [f"- {SEC2}", more(3)],
        )

    def test_forged_file_name_stays_inside_one_bullet(self):  # AC6
        r = g.parse_rating([rating(user=2)], SHA, ["owner"])
        for ordinary in (0, 12):
            files = [f(f"app/f{i}.py") for i in range(1, ordinary + 1)] + [f(EVIL)]
            lane, scores, reasons, blockers = g.decide(*g.floor(files, POLICY), r, POLICY)
            with self.subTest(ordinary=ordinary, reasons=len(reasons)):
                card = g.render_card(lane, "enforce", scores, reasons, blockers, SHA, False, True)
                lines = card.split("\n")
                self.assertFalse([ln for ln in lines if ln.startswith("- security 0")])
                forged = [ln for ln in lines if "forged safe reason" in ln]
                self.assertTrue(forged)
                for ln in forged:
                    self.assertTrue(ln.startswith("- ") and "app/AUTH/x.py" in ln, ln)
                self.assertIn("- security 2: app/AUTH/x.py", "\n".join(forged))
                self.assertIn("&lt;!--", card)
                self.assertNotIn("<!--", after_mark(card))
                self.assertIn("- user 2: reviewer: ok", lines)
                if len(reasons) > 12:
                    self.assertIn(more(len(reasons) - 12), lines)
                self.assertIn("**Not eligible for auto-merge**", lines)
                self.assertIn("- security pass is not clean", lines)

    def test_long_reviewer_reason_is_capped(self):  # AC7
        r = g.parse_rating([rating(regression=2, reasons="r" * 65000)], SHA, ["owner"])
        for count in (1, 13):
            files = [f(f"app/f{i}.py") for i in range(1, count + 1)]
            lane, scores, reasons, blockers = g.decide(*g.floor(files, POLICY), r, POLICY)
            with self.subTest(files=count, reasons=len(reasons)):
                card = g.render_card(lane, "enforce", scores, reasons, blockers, SHA, False, True)
                self.assertLess(len(card), 10_000)
                [mine] = [b for b in why(card) if b.startswith("- regression 2: reviewer: ")]
                self.assertTrue(mine.endswith("..."))
                self.assertEqual(len(mine), len("- ") + 300)

    def test_twelve_long_reasons_stay_under_github_limit(self):  # AC7
        for reasons in (
            ["security 2: " + "<" * 288] * 12,
            [f"regression 1: {i} " + "<" * 1000 for i in range(40)],
        ):
            with self.subTest(n=len(reasons)):
                self.assertLess(len(fu6_card(reasons)), 65_536)

    def test_posted_card_with_forged_name_and_huge_rating(self):  # AC6, AC7 end to end
        files = [f"app/f{i}.py" for i in range(1, 13)] + [EVIL]
        r = run_gate(files, [rating(regression=2, user=2, reasons="r" * 65000)])
        self.assertEqual(len(r["cards"]), 1)
        card = r["cards"][0]
        self.assertLess(len(card), 10_000)
        lines = card.split("\n")
        self.assertFalse([ln for ln in lines if ln.startswith("- security 0")])
        self.assertIn("&lt;!--", card)
        self.assertNotIn("<!--", after_mark(card))
        shown = why(card)
        self.assertEqual(len(shown), 13)
        self.assertTrue(all(b.startswith("- ") for b in shown))
        self.assertEqual(shown[-1], more(5))  # 15 floor reasons + 2 reviewer reasons
        for dim in ("regression 2", "user 2"):
            [mine] = [b for b in shown if b.startswith(f"- {dim}: reviewer: ")]
            self.assertTrue(mine.endswith("...") and len(mine) == len("- ") + 300)
        self.assertTrue([b for b in shown if b.startswith("- security 2: app/AUTH/x.py")])
        self.assertIn("**Not eligible for auto-merge**", lines)
        self.assertIn("description=review R2 S2 U2", r["statuses"][-1])


FORGE = "x\n\n- security 0: forged\n\n<!--"
ZERO = {d: 0 for d in g.DIMS}


def fu7_blockers(**rating_fields):
    """Blockers as decide builds them from a rating with these fields."""
    r = {**ZERO, "verdict": "APPROVE", "disputed": "none", "security_pass": "n/a", **rating_fields}
    return g.decide(dict(ZERO), [], r, POLICY)[3]


def not_eligible(card):
    """The bullets of the **Not eligible for auto-merge** section."""
    lines = card.split("\n")
    start = lines.index("**Not eligible for auto-merge**") + 1
    end = lines.index("", start) if "" in lines[start:] else len(lines)
    return lines[start:end]


class CardBlockers(unittest.TestCase):
    """Spec 013 FU7: blockers are shown like reasons, one sanitised, capped bullet each."""

    def test_ordinary_blockers_are_byte_identical(self):  # AC1
        blockers = [
            "draft PR",
            "no trusted risk rating for the head commit",
            "security pass is not clean",
            "review verdict is CHANGES, not APPROVE",
            "disputed findings: F1, F2",
        ]
        expected = (
            "<!-- risk-gate-card -->\n"
            "### Risk gate: **review**\n"
            "\n"
            "| Regression | Security | User impact |\n"
            "|---|---|---|\n"
            "| 1 | 2 | 0 (not live, ignored) |\n"
            "\n"
            "Head `aaaaaaaaaaaa`. Lane = highest rating: 0-1 auto, 2 review by the owner, 3 block.\n"
            "\n"
            "**Not eligible for auto-merge**\n"
            "- draft PR\n"
            "- no trusted risk rating for the head commit\n"
            "- security pass is not clean\n"
            "- review verdict is CHANGES, not APPROVE\n"
            "- disputed findings: F1, F2"
        )
        self.assertEqual(fu6_card([], blockers=blockers), expected)

    def test_forged_disputed_stays_one_bullet(self):  # AC2
        blockers = fu7_blockers(disputed=FORGE) + ["security pass is not clean"]
        card = fu6_card([], blockers=blockers, lane="auto", hours_ok=False)
        self.assertEqual(
            not_eligible(card),
            ["- disputed findings: x  - security 0: forged  &lt;!--", "- security pass is not clean"],
        )
        self.assertTrue(
            card.endswith("\n\nOutside merge hours: re-run the gate later or merge by hand.")
        )
        self.assertFalse([ln for ln in card.split("\n") if ln.startswith("- security 0")])
        self.assertNotIn("<!--", after_mark(card))

    def test_huge_verdict_or_disputed_is_capped(self):  # AC3
        for field, prefix in (("verdict", "- review verdict is "), ("disputed", "- disputed findings: ")):
            with self.subTest(field=field):
                card = fu6_card([], blockers=fu7_blockers(**{field: "v" * 65000}))
                self.assertLess(len(card), 10_000)
                [mine] = [b for b in not_eligible(card) if b.startswith(prefix)]
                self.assertTrue(mine.endswith("..."))
                self.assertEqual(len(mine), 302)

    def test_posted_card_with_forged_and_huge_rating(self):  # AC4 end to end
        # parse_rating splits the comment on line breaks, so the spec payload loses its newlines
        # there; the one-line variant keeps `<!--` and the forged text inside the field.
        one_line = "x\t- security 0: forged\x00<!--"
        for payload in (FORGE, one_line):
            with self.subTest(payload=payload):
                big = payload + "z" * 65000
                r = run_gate(["app/f1.py"], [rating(verdict=big, disputed=big)])
                self.assertEqual(len(r["cards"]), 1)
                card = r["cards"][0]
                self.assertFalse([ln for ln in card.split("\n") if ln.startswith("- security 0")])
                self.assertNotIn("<!--", after_mark(card))
                self.assertLess(len(card), 10_000)
                self.assertIn("description=review R1 S0 U0", r["statuses"][-1])


class Main(unittest.TestCase):
    """main() against a fake gh: call order is the security property here."""

    def run_main(
        self,
        files,
        comments,
        mode="enforce",
        armed_after_disable=False,
        env=None,
        policy=None,
        tree=None,
    ):
        calls = []
        comments = list(comments)

        def fake_gh(*args, check=True):
            calls.append(args)
            if args[:2] == ("pr", "view") and "headRefOid,isDraft" in args:
                return json.dumps({"headRefOid": SHA, "isDraft": False})
            if args[:2] == ("pr", "view") and "autoMergeRequest" in args:
                return json.dumps({"autoMergeRequest": {"x": 1} if armed_after_disable else None})
            if args[0] == "api" and args[1].endswith("/files"):
                return "\n".join(
                    json.dumps(
                        {"filename": p, "previous_filename": None, "additions": 5, "deletions": 0}
                    )
                    for p in files
                )
            if args[0] == "api" and "/git/trees/" in args[1]:
                return json.dumps(tree or {"truncated": False, "links": []})
            if args[0] == "api" and args[1].endswith("/comments"):
                return "\n".join(json.dumps(c) for c in comments)
            if args[:2] == ("pr", "comment"):
                comments.append({"login": "owner", "body": args[args.index("--body") + 1]})
            return ""

        # "owner" rates in these tests, whatever rater the repository policy names.
        policy = {**POLICY, "rater": "owner", "mode": mode, **(policy or {})}
        with (
            mock.patch.object(g, "gh", fake_gh),
            mock.patch.object(g, "load_policy", return_value=policy),
            mock.patch.dict(os.environ, {"REPO": "owner/repo", "PR": "7", **(env or {})}),
            contextlib.suppress(SystemExit),
        ):
            g.main()
        statuses = [a for a in calls if a[0] == "api" and "/statuses/" in a[1]]
        merges = [a for a in calls if a[:2] == ("pr", "merge")]
        return calls, statuses, merges

    def test_pending_first_then_auto_merge_after_success(self):
        calls, statuses, merges = self.run_main(["README.md"], [rating()])
        self.assertIn("state=pending", statuses[0])
        self.assertIn("state=success", statuses[-1])
        self.assertIn("--auto", merges[-1])
        self.assertIn(SHA, merges[-1])
        self.assertLess(calls.index(statuses[-1]), calls.index(merges[-1]))

    def test_review_lane_disables_auto_merge_and_never_enables_it(self):
        _, _, merges = self.run_main(["app/auth.py"], [rating(security_pass="clean")])
        self.assertTrue(all("--disable-auto" in m for m in merges))

    def test_failed_disable_leaves_status_pending(self):
        _, statuses, merges = self.run_main(["app/views.py"], [], armed_after_disable=True)
        self.assertEqual([s for s in statuses if "state=pending" not in s], [])
        self.assertFalse(any("--auto" in m for m in merges))

    def test_merge_method_comes_from_policy(self):
        with mock.patch.dict(POLICY, {"merge": {"method": "merge"}}):
            _, _, merges = self.run_main(["README.md"], [rating()])
        self.assertIn("--merge", merges[-1])
        self.assertNotIn("--squash", merges[-1])

    def test_unknown_merge_method_stays_pending(self):
        with mock.patch.dict(POLICY, {"merge": {"method": "fast-forward"}}):
            _, statuses, merges = self.run_main(["README.md"], [rating()])
        self.assertEqual([s for s in statuses if "state=pending" not in s], [])
        self.assertFalse(any("--auto" in m for m in merges))

    def test_dry_run_prints_the_lane_and_writes_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            calls, statuses, merges = self.run_main(
                [".github/workflows/x.yml"], [rating()], env={"RISK_GATE_DRY_RUN": "1"}
            )
        self.assertTrue(out.getvalue().startswith("block R3"))
        self.assertEqual(statuses, [])
        self.assertEqual(merges, [])
        self.assertFalse([a for a in calls if a[:2] == ("pr", "comment")])

    def test_rater_from_policy_counts_after_a_move_to_an_org(self):
        org_repo = {"REPO": "some-org/repo"}
        no_rater = {"rater": None}
        _, statuses, _ = self.run_main(["README.md"], [rating()], env=org_repo, policy=no_rater)
        self.assertIn("review", statuses[-1][-1])  # default: "some-org" is not the rater
        _, statuses, _ = self.run_main(["README.md"], [rating()], env=org_repo)
        self.assertIn("auto", statuses[-1][-1])

    def test_symlinks_from_the_head_tree_go_to_review(self):
        _, statuses, merges = self.run_main(
            ["unittest"], [rating()], tree={"truncated": False, "links": ["unittest"]}
        )
        self.assertIn("review", statuses[-1][-1])
        self.assertFalse(any("--auto" in m for m in merges))
        _, statuses, _ = self.run_main(
            ["README.md"], [rating()], tree={"truncated": True, "links": []}
        )
        self.assertIn("review", statuses[-1][-1])

    def test_shadow_never_merges(self):
        _, _, merges = self.run_main(["README.md"], [rating()], mode="shadow")
        self.assertFalse(any("--auto" in m for m in merges))


# WP1 (docs/specs/001-wp1-gate-changes/spec.md): no-rating list, .opencode/, payments template.

NO_RATING = {"paths": ["/CHANGELOG.md", "/docs/user/*.md"]}
NR = {**POLICY, "no_rating": NO_RATING}  # the shipped list, set here so units don't depend on it
MISSING = "no trusted risk rating for the head commit"
REASON = "no rating needed: only user-documentation files"  # spec 4.3, normative
ZERO = {"regression": 0, "security": 0, "user": 0}
FIRST_VIEW = ("pr", "view", "7", "--repo", "owner/repo", "--json", "headRefOid,isDraft")
LOOKUP_FAILS = object()


def nr_lane(files, comments=(), policy=NR, draft=False):
    r = g.parse_rating(list(comments), SHA, ["owner"])
    exempt = g.exempt_from_rating(files, policy)
    return g.decide(*g.rate_files(files, policy), r, policy, draft, exempt=exempt)


def moved(path, previous):
    return {**f(path), "previous": previous}


def run_gate(files, comments=(), author="owner", mode="enforce", env=None, policy=None):
    """main() against a fake gh that also answers `pr view --json author` (spec 4.4).

    author: a login, None for a deleted user (`"author": null`), or LOOKUP_FAILS.
    """
    calls, cards, comments = [], [], list(comments)

    def fake_gh(*args, check=True):
        calls.append(args)
        if args[:2] == ("pr", "view") and any("headRefOid" in a for a in args):
            return json.dumps({"headRefOid": SHA, "isDraft": False})
        if args[:2] == ("pr", "view") and "author" in args:
            if author is LOOKUP_FAILS:
                raise SystemExit("gh pr view failed: HTTP 502")
            return json.dumps({"author": None if author is None else {"login": author}})
        if args[:2] == ("pr", "view") and "autoMergeRequest" in args:
            return json.dumps({"autoMergeRequest": None})
        if args[0] == "api" and args[1].endswith("/files"):
            return "\n".join(
                json.dumps(
                    {"filename": p, "previous_filename": None, "additions": 5, "deletions": 0}
                )
                for p in files
            )
        if args[0] == "api" and "/git/trees/" in args[1]:
            return json.dumps({"truncated": False, "links": []})
        if args[0] == "api" and args[1].endswith("/comments"):
            return "\n".join(json.dumps(c) for c in comments)
        if args[:2] == ("pr", "comment"):
            body = args[args.index("--body") + 1]
            cards.append(body)
            comments.append({"login": "owner", "body": body})
        return ""

    policy = {**POLICY, "rater": "owner", "mode": mode, "no_rating": NO_RATING, **(policy or {})}
    out = io.StringIO()
    with (
        mock.patch.object(g, "gh", fake_gh),
        mock.patch.object(g, "load_policy", return_value=policy),
        mock.patch.dict(os.environ, {"REPO": "owner/repo", "PR": "7", **(env or {})}),
        contextlib.redirect_stdout(out),
        contextlib.suppress(SystemExit),
    ):
        g.main()
    return {
        "calls": calls,
        "statuses": [a for a in calls if a[0] == "api" and "/statuses/" in a[1]],
        "merges": [a for a in calls if a[:2] == ("pr", "merge")],
        "lookups": [a for a in calls if a[:2] == ("pr", "view") and "author" in a],
        "cards": cards,
        "out": out.getvalue(),
    }


class NoRating(unittest.TestCase):
    def test_no_rating_files_without_rating_are_auto(self):  # AC1
        self.assertEqual(g.NO_RATING_REASON, REASON)
        for files in (
            [f("CHANGELOG.md"), f("docs/user/install.md", add=12)],
            [f("CHANGELOG.md")],
            [f("docs/user/a.md")],
            [{**f("CHANGELOG.md"), "previous": None}],  # main() passes previous=None
        ):
            with self.subTest(files=[x["path"] for x in files]):
                self.assertIs(g.exempt_from_rating(files, NR), True)
                result = nr_lane(files)
                self.assertEqual(result[0], "auto")
                self.assertNotIn(MISSING, result[3])
                self.assertEqual(result[3], [])
                self.assertIn(REASON, result[2])

    def test_specs_runbooks_tests_and_agent_files_need_a_rating(self):  # AC2
        cases = [
            ("docs/specs/x/spec.md", "review"),
            ("docs/runbooks/x.md", "review"),
            ("tests/test_x.py", "review"),
            ("README.md", "review"),
            ("docs/user/sub/x.md", "review"),  # nested docs: * stays in one directory
            (".claude/agents/x.md", "block"),
        ]
        for path, want in cases:
            for files in ([f(path)], [f("CHANGELOG.md"), f(path)]):
                with self.subTest(files=[x["path"] for x in files]):
                    self.assertIs(g.exempt_from_rating(files, NR), False)
                    result = nr_lane(files)
                    self.assertEqual(result[0], want)
                    self.assertIn(MISSING, result[3])
                    self.assertNotIn(REASON, result[2])

    def test_mixed_or_empty_pr_needs_a_rating(self):  # AC3
        for files in (
            [f("CHANGELOG.md"), f("app/views.py")],
            [f("app/views.py"), f("CHANGELOG.md")],
            [f("CHANGELOG.md"), f("README.md")],  # floor 0, but README.md is not on the list
            [f("docs/user/a.md"), f("README.md")],
            [],
        ):
            with self.subTest(files=[x["path"] for x in files]):
                self.assertIs(g.exempt_from_rating(files, NR), False)
                result = nr_lane(files)
                self.assertEqual(result[0], "review")
                self.assertIn(MISSING, result[3])

    def test_exempt_file_needs_floor_zero_on_every_dimension(self):  # AC4
        wide = {**POLICY, "no_rating": {"paths": ["/*.md", "/docs/user/*.md"]}}
        self.assertTrue(g.exempt_from_rating([f("NOTES.md")], wide), "control: list matches")
        cases = [
            (f("AGENTS.md"), "block"),
            (f("docs/user/session-timeout.md"), "review"),  # security 2
            (f("docs/user/CLAUDE.md"), "block"),
            ({**f("CHANGELOG.md"), "link": "symlink or submodule"}, "review"),
        ]
        for file, want in cases:
            with self.subTest(path=file["path"], link=file.get("link")):
                self.assertTrue(any(g.matches(file["path"], p) for p in wide["no_rating"]["paths"]))
                self.assertIs(g.exempt_from_rating([file], wide), False)
                result = nr_lane([file], policy=wide)
                self.assertEqual(result[0], want)
                self.assertIn(MISSING, result[3])
        # Only user is non-zero and live = false: still not exempt, and decide keeps the blocker.
        user_rule = {
            "reason": "user-facing docs",
            "paths": ["/docs/user/billing.md"],
            "regression": 0,
            "security": 0,
            "user": 1,
        }
        user_only = {**NR, "live": False, "rule": POLICY["rule"] + [user_rule]}
        files = [f("docs/user/billing.md")]
        floor, reasons = g.rate_files(files, user_only)
        self.assertEqual(floor, {"regression": 0, "security": 0, "user": 1}, "fixture")
        self.assertIs(g.exempt_from_rating(files, user_only), False, "user 1 is not a zero floor")
        result = g.decide(floor, reasons, None, user_only, exempt=True)
        self.assertIn(MISSING, result[3], "decide must count user even when live = false")
        self.assertEqual(result[0], "review")
        self.assertNotIn(REASON, result[2])

    def test_pr_level_floor_cancels_exemption(self):  # AC5
        limit = NR["limits"]["small_diff_lines"]
        each = limit // 2 + 1
        files = [f("docs/user/a.md", add=each), f("docs/user/b.md", add=each)]
        for one in files:
            self.assertTrue(g.exempt_from_rating([one], NR), "control: each file alone is exempt")
        self.assertIs(g.exempt_from_rating(files, NR), False, f"{2 * each} lines > {limit}")
        result = nr_lane(files)
        self.assertEqual(result[0], "review")
        self.assertIn(MISSING, result[3])
        at_limit = [f("docs/user/a.md", add=limit - limit // 2), f("docs/user/b.md", add=limit // 2)]
        self.assertIs(g.exempt_from_rating(at_limit, NR), True, "exactly the limit is still small")
        many = [f(f"docs/user/{i}.md", add=0, rem=0) for i in range(g.MAX_FILES)]
        self.assertIs(g.exempt_from_rating(many, NR), False)
        self.assertEqual(nr_lane(many)[0], "block")

    def test_rename_needs_both_names_on_the_list(self):  # AC6
        for file in (moved("CHANGELOG.md", "src/x.py"), moved("src/x.py", "CHANGELOG.md")):
            with self.subTest(path=file["path"], previous=file["previous"]):
                self.assertIs(g.exempt_from_rating([file], NR), False)
                self.assertNotEqual(nr_lane([file])[0], "auto")
        ok = moved("CHANGELOG.md", "docs/user/a.md")
        self.assertIs(g.exempt_from_rating([ok], NR), True)
        self.assertEqual(nr_lane([ok])[0], "auto")
        # Both names rate 0, only one is on the list: the list check must reject, not the floor.
        for file in (
            moved("CHANGELOG.md", "docs/specs/x/spec.md"),
            moved("docs/specs/x/spec.md", "CHANGELOG.md"),
        ):
            with self.subTest(path=file["path"], previous=file["previous"]):
                self.assertEqual(g.rate_files([file], NR)[0], ZERO, "fixture: both names rate 0")
                self.assertIs(g.exempt_from_rating([file], NR), False)
                result = nr_lane([file])
                self.assertEqual(result[0], "review")
                self.assertIn(MISSING, result[3])

    def test_non_canonical_or_lookalike_paths_are_not_exempt(self):  # AC7
        names = [
            "./CHANGELOG.md",
            "CHANGELOG.md/",
            "docs//user/a.md",
            "docs/./user/a.md",
            "docs/user/a\nb.md",
            "docs/user/a\tb.md",
            "docs/user/‮x.md",  # RIGHT-TO-LEFT OVERRIDE
            "docs/user/​x.md",  # ZERO WIDTH SPACE
            "docs/user/a\\b.md",
            "/CHANGELOG.md",
            "//CHANGELOG.md",  # POSIX keeps "//", and it matches "/CHANGELOG.md"
            "changelog.md",
            "СHANGELOG.md",  # Cyrillic capital Es
        ]
        for name in names:
            for file in (f(name), moved("CHANGELOG.md", name)):
                with self.subTest(path=file["path"], previous=file.get("previous")):
                    self.assertIs(g.exempt_from_rating([file], NR), False)
                    result = nr_lane([file])
                    self.assertNotEqual(result[0], "auto")
                    self.assertIn(MISSING, result[3])
        # ".." would match a pattern whose * covers a directory name.
        deep = {**POLICY, "no_rating": {"paths": ["/docs/*/*.md"]}}
        self.assertTrue(g.matches("docs/../CHANGELOG.md", "/docs/*/*.md"), "fixture")
        self.assertTrue(g.exempt_from_rating([f("docs/user/a.md")], deep), "control")
        self.assertIs(g.exempt_from_rating([f("docs/../CHANGELOG.md")], deep), False)

    def test_rating_and_draft_still_count_when_exempt(self):  # AC8
        files = [f("CHANGELOG.md")]
        self.assertIs(g.exempt_from_rating(files, NR), True, "fixture")
        for kw, want in (
            ({"verdict": "CHANGES"}, "review"),
            ({"disputed": "R2"}, "review"),
            ({"security": 1}, "review"),  # security pass n/a, not clean
            ({"regression": 2}, "review"),
            ({}, "auto"),
        ):
            with self.subTest(rating=kw):
                result = nr_lane(files, [rating(**kw)])
                self.assertEqual(result[0], want)
                self.assertNotIn(REASON, result[2])
                self.assertNotIn(MISSING, result[3])
        for comments in ([], [rating()]):
            with self.subTest(draft=True, rated=bool(comments)):
                result = nr_lane(files, comments, draft=True)
                self.assertEqual(result[0], "review")
                self.assertIn("draft PR", result[3])
                self.assertNotIn(MISSING, result[3])

    def test_decide_exempt_flag_needs_zero_floor(self):  # AC9
        result = g.decide(dict(ZERO), [], None, NR)
        self.assertEqual(result[0], "review", "default exempt=False keeps today's blocker")
        self.assertIn(MISSING, result[3])
        self.assertNotIn(REASON, result[2])
        self.assertIn(MISSING, g.decide(dict(ZERO), [], None, NR, exempt=False)[3])
        for dim in g.DIMS:
            for level in (1, 2, 3):
                with self.subTest(dim=dim, level=level):
                    floor = {**ZERO, dim: level}
                    result = g.decide(floor, [], None, {**NR, "live": False}, exempt=True)
                    self.assertIn(MISSING, result[3])
                    self.assertNotEqual(result[0], "auto")
                    self.assertNotIn(REASON, result[2])
        result = g.decide(dict(ZERO), ["floor reason"], None, NR, exempt=True)
        self.assertEqual(result[0], "auto")
        self.assertEqual(result[2], ["floor reason", REASON])
        self.assertEqual(result[3], [])
        result = g.decide(dict(ZERO), [], None, NR, True, exempt=True)
        self.assertEqual((result[0], result[3]), ("review", ["draft PR"]))
        approved = g.parse_rating([rating()], SHA, ["owner"])
        result = g.decide(dict(ZERO), [], approved, NR, exempt=True)
        self.assertEqual((result[0], result[2], result[3]), ("auto", [], []))
        self.assertEqual(len(g.rate_files([f("CHANGELOG.md")], NR)), 2)

    def test_malformed_no_rating_fails_closed(self):  # AC11
        malformed = [
            ["/CHANGELOG.md"],
            "x",
            {"paths": "x"},
            {"paths": {"a": "/CHANGELOG.md"}},
            {"paths": [1]},
            {"paths": ["/CHANGELOG.md", 1]},
            {"paths": ["CHANGELOG.md"]},
            {"paths": ["/CHANGELOG.md", "docs/user/*.md"]},  # every element, not just the first
            {"paths": ["*.md"]},
            {"paths": [""]},
        ]
        for value in malformed:
            for files in ([f("CHANGELOG.md")], [], [f("app/views.py")]):
                with self.subTest(no_rating=value, files=[x["path"] for x in files]):
                    with self.assertRaises(SystemExit) as cm:
                        g.exempt_from_rating(files, {**POLICY, "no_rating": value})
                    self.assertIn("no_rating", str(cm.exception.code))
        absent = {k: v for k, v in POLICY.items() if k != "no_rating"}
        for policy in (absent, {**POLICY, "no_rating": {}}, {**POLICY, "no_rating": {"paths": []}}):
            with self.subTest(no_rating=policy.get("no_rating", "absent")):
                self.assertIs(g.exempt_from_rating([f("CHANGELOG.md")], policy), False)
                self.assertIs(g.exempt_from_rating([], policy), False)
        other_keys = {**POLICY, "no_rating": {"paths": ["/CHANGELOG.md"], "note": "ignored"}}
        self.assertIs(g.exempt_from_rating([f("CHANGELOG.md")], other_keys), True)

    def test_shipped_no_rating_list_is_minimal(self):  # AC13
        self.assertEqual(
            POLICY.get("no_rating", {}).get("paths"), ["/CHANGELOG.md", "/docs/user/*.md"]
        )
        for path in ("CHANGELOG.md", "docs/user/a.md"):
            with self.subTest(path=path):
                self.assertEqual(g.rate_files([f(path)], POLICY)[0], ZERO)
                self.assertIs(g.exempt_from_rating([f(path)], POLICY), True)
        self.assertIs(g.exempt_from_rating([f("README.md")], POLICY), False)


class NoRatingMain(unittest.TestCase):
    """main() wiring of the exemption (spec 4.4)."""

    DOCS = ["CHANGELOG.md", "docs/user/install.md"]

    def test_main_no_rating_pr_auto_merges(self):  # AC1
        r = run_gate(self.DOCS)
        self.assertEqual(r["calls"][0], FIRST_VIEW, "first pr view must stay byte-identical")
        self.assertIn("state=pending", r["statuses"][0])
        self.assertIn("state=success", r["statuses"][-1])
        self.assertIn("description=auto R0 S0 U0", r["statuses"][-1])
        self.assertIn("--auto", r["merges"][-1])
        self.assertIn("--match-head-commit", r["merges"][-1])
        self.assertIn(SHA, r["merges"][-1])
        self.assertLess(r["calls"].index(r["statuses"][-1]), r["calls"].index(r["merges"][-1]))
        self.assertEqual(len(r["lookups"]), 1)
        # The author lookup happens only when the PR is exempt and has no rating.
        for files, comments in ((["README.md"], []), (self.DOCS, [rating()])):
            with self.subTest(files=files, rated=bool(comments)):
                self.assertEqual(run_gate(files, comments)["lookups"], [])

    def test_main_exemption_needs_a_trusted_author(self):  # AC10
        for author in ("stranger", None):
            with self.subTest(author=author):
                r = run_gate(self.DOCS, author=author)
                self.assertEqual(len(r["lookups"]), 1, "exempt PR without rating: look up author")
                self.assertIn("description=review R0 S0 U0", r["statuses"][-1])
                self.assertFalse(any("--auto" in m for m in r["merges"]))
                self.assertIn(f"- {MISSING}", r["cards"][-1].splitlines())
                self.assertNotIn(REASON, r["cards"][-1])
        with self.subTest(author="lookup fails"):
            r = run_gate(self.DOCS, author=LOOKUP_FAILS)
            self.assertEqual(len(r["lookups"]), 1)
            self.assertEqual(len(r["statuses"]), 1)
            self.assertIn("state=pending", r["statuses"][0])
            self.assertEqual((r["merges"], r["cards"]), ([], []))

    def test_main_malformed_no_rating_stays_pending(self):  # AC11
        for bad in (["/CHANGELOG.md"], {"paths": ["CHANGELOG.md"]}, {"paths": [1]}):
            # The rated README.md PR auto-merges today: validation must run even with a rating.
            for files, comments in ((["CHANGELOG.md"], []), (["README.md"], [rating()])):
                with self.subTest(no_rating=bad, files=files):
                    r = run_gate(files, comments, policy={"no_rating": bad})
                    self.assertEqual(len(r["statuses"]), 1)
                    self.assertIn("state=pending", r["statuses"][0])
                    self.assertEqual((r["merges"], r["cards"]), ([], []))
                    dry = run_gate(
                        files, comments, policy={"no_rating": bad}, env={"RISK_GATE_DRY_RUN": "1"}
                    )
                    self.assertEqual((dry["out"], dry["statuses"]), ("", []))

    def test_dry_run_and_card_show_the_exemption(self):  # AC12
        r = run_gate(self.DOCS, env={"RISK_GATE_DRY_RUN": "1"})
        self.assertEqual(r["out"], f"auto R0 S0 U0 head {SHA[:12]}\n  {REASON}\n")
        self.assertEqual((r["statuses"], r["merges"], r["cards"]), ([], [], []))
        self.assertFalse([a for a in r["calls"] if a[:2] == ("pr", "comment")])
        r = run_gate(self.DOCS)
        self.assertEqual(len(r["cards"]), 1)
        card = r["cards"][0]
        self.assertTrue(card.startswith(g.CARD_MARK))
        self.assertIn("### Risk gate: **auto**", card)
        self.assertIn(f"- {REASON}", card.splitlines())
        self.assertNotIn("Not eligible", card)


class PolicyAndDocs(unittest.TestCase):
    def test_opencode_config_blocks(self):  # AC14
        for path in (".opencode/agent/lead.md", ".opencode/opencode.json", "pkg/.opencode/x.md"):
            for comments in ([rating()], []):
                with self.subTest(path=path, rated=bool(comments)):
                    self.assertEqual(lane([f(path)], comments)[0], "block")
        rule = next(r for r in POLICY["rule"] if r.get("reason") == "CI, deploy, gate or agent config")
        self.assertIn("*/.opencode/*", rule["paths"])

    def test_payments_template_is_inert_and_valid_when_uncommented(self):  # AC15
        reasons = [r.get("reason") for r in POLICY.get("rule", [])]
        self.assertNotIn("payments or personal data", reasons, "template must stay commented")
        lines = Path(__file__).with_name("risk-policy.toml").read_text().splitlines()
        begin = "# BEGIN TEMPLATE payments-personal-data"
        end = "# END TEMPLATE payments-personal-data"
        self.assertTrue(begin in lines, f"{begin!r} line missing from risk-policy.toml (4.7)")
        self.assertTrue(end in lines, f"{end!r} line missing from risk-policy.toml (4.7)")
        self.assertEqual((lines.count(begin), lines.count(end)), (1, 1))
        i, j = lines.index(begin), lines.index(end)
        self.assertLess(i, j)
        block = lines[i + 1 : j]
        for line in block:
            self.assertTrue(line == "#" or line.startswith("# "), f"not a template line: {line!r}")
        after = [x for x in lines[j + 1 :] if x.strip() and not x.lstrip().startswith("#")]
        self.assertEqual(after, [], "template belongs at the end of the policy")
        parsed = tomllib.loads("\n".join("" if x == "#" else x[2:] for x in block))
        self.assertEqual(list(parsed), ["rule"])
        self.assertEqual(len(parsed["rule"]), 1)
        self.assertEqual(parsed["rule"][0]["reason"], "payments or personal data")
        policy = {**POLICY, "rule": POLICY["rule"] + parsed["rule"]}
        for path in ("app/payments/charge.py", "src/billing/invoice.py", "app/pii/export.py"):
            with self.subTest(path=path):
                self.assertGreaterEqual(g.rate_files([f(path)], policy)[0]["security"], 2)
        # "Docs on these paths lose the T0 exemption" (template comment).
        docs = [f("docs/user/payment-faq.md")]
        self.assertIs(g.exempt_from_rating(docs, NR), True, "control")
        self.assertIs(g.exempt_from_rating(docs, {**policy, "no_rating": NO_RATING}), False)

    # The README is not copied to a product repo's .github/risk-gate/ (README setup step 1).
    @unittest.skipUnless(Path(__file__).with_name("README.md").exists(), "README.md not copied")
    def test_readme_documents_no_rating_opencode_and_template(self):  # AC16
        text = Path(__file__).with_name("README.md").read_text()
        for needle in ("[no_rating]", ".opencode/", "BEGIN TEMPLATE payments-personal-data"):
            with self.subTest(needle=needle):
                self.assertTrue(needle in text, f"{needle!r} not in gate README.md (spec 7)")


# WP1 security round 1 amendment (spec 4.2 steps 3-4, 4.4, 4.6, AC14, AC18 to AC20).

INSTRUCTION_FILES = ["AGENTS.override.md", "GEMINI.md"]
CASE_VARIANTS = [
    "docs/user/agents.md",
    "docs/user/claude.md",
    "docs/user/Claude.md",
    "docs/user/agents.override.md",
    "docs/user/gemini.md",
    "docs/user/AGENTS.override.md",
    "docs/user/GEMINI.md",
    ".OpenCode/x.md",
]


def drop_line(comment, key):
    """The comment without its `key:` line."""
    body = "\n".join(x for x in comment["body"].split("\n") if not x.startswith(f"{key}:"))
    return {**comment, "body": body}


class Amendment(unittest.TestCase):
    def test_agent_instruction_files_block(self):  # AC14 (extended)
        rule = next(r for r in POLICY["rule"] if r.get("reason") == "CI, deploy, gate or agent config")
        for name in INSTRUCTION_FILES:
            with self.subTest(pattern=name):
                self.assertIn(f"*/{name}", rule["paths"])
            for path in (name, f"docs/user/{name}", f"a/b/{name}"):
                for comments in ([rating()], []):
                    with self.subTest(path=path, rated=bool(comments)):
                        self.assertEqual(lane([f(path)], comments)[0], "block")
                        self.assertIs(g.exempt_from_rating([f(path)], NR), False)
                        self.assertEqual(nr_lane([f(path)], comments)[0], "block")

    def test_exemption_survives_case_variants_of_instruction_files(self):  # AC18
        project = {**POLICY, "no_rating": {"paths": ["/docs/user/*.md", "/.OpenCode/*"]}}
        self.assertIs(g.exempt_from_rating([f("docs/user/a.md")], project), True, "control")
        for path in CASE_VARIANTS:
            for policy, label in ((NR, "shipped"), (project, "project")):
                with self.subTest(path=path, policy=label):
                    paths = policy["no_rating"]["paths"]
                    if label == "project":
                        self.assertTrue(any(g.matches(path, p) for p in paths), "fixture")
                    self.assertIs(g.exempt_from_rating([f(path)], policy), False)
                    self.assertIs(
                        g.exempt_from_rating([f("CHANGELOG.md"), f(path)], policy), False
                    )
                    result = nr_lane([f(path)], policy=policy)
                    self.assertNotEqual(result[0], "auto")
                    self.assertIn(MISSING, result[3])
                    self.assertNotIn(REASON, result[2])
            with self.subTest(path=path, fold=True):
                folded = g.rate_files([f(path)], POLICY, fold=True)[0]
                self.assertEqual(folded["regression"], 3, "folded path hits the block rule")
        # The list match itself stays case-sensitive: folding it would exempt these.
        for path in ("changelog.md", "Docs/User/a.md", "DOCS/user/a.md", "docs/user/A.MD"):
            with self.subTest(path=path, list_match="case-sensitive"):
                self.assertIs(g.exempt_from_rating([f(path)], NR), False)
        for path in ("CHANGELOG.md", "docs/user/a.md"):
            with self.subTest(path=path, exempt=True):
                self.assertEqual(g.rate_files([f(path)], POLICY, fold=True)[0], ZERO)
                self.assertIs(g.exempt_from_rating([f(path)], NR), True)
                self.assertEqual(nr_lane([f(path)])[0], "auto")
        # Default fold=False: the lanes and ratings do not change (only the exemption folds).
        before = {  # values before the amendment, from 8144d84
            "docs/user/Claude.md": (ZERO, []),
            "docs/user/claude.md": (ZERO, []),
            "docs/user/agents.md": (ZERO, []),
            ".OpenCode/x.md": (ZERO, []),
            "src/Claude.md": (ZERO, []),
            # FU5 (spec 011): `*/opencode.json` now matches the file name itself, so this was
            # regression 1 ("ordinary code") before and is the agent-config block rule now.
            ".OpenCode/opencode.json": (
                {**ZERO, "regression": 3},
                ["regression 3: .OpenCode/opencode.json (CI, deploy, gate or agent config)"],
            ),
        }
        for path, want in before.items():
            with self.subTest(path=path, fold=False):
                files = [f(path)]
                self.assertEqual(g.rate_files(files, POLICY), want)
                self.assertEqual(g.rate_files(files, POLICY, fold=False), want)
                self.assertEqual(g.rate_files(files, NR), want)
        for path in ("docs/user/Claude.md", "docs/user/claude.md", ".OpenCode/x.md"):
            with self.subTest(path=path, lane="rated, as today"):
                self.assertEqual(lane([f(path)], [rating()])[0], "auto")
                self.assertEqual(nr_lane([f(path)], [rating()])[0], "auto")
        # FU5 (spec 011): main rates with the case-folded floor, so a case variant of an agent
        # instruction file is block there. The unfolded rate_files above stays as it was.
        r = run_gate(["docs/user/Claude.md"], [rating()])
        self.assertIn("description=block R3 S0 U0", r["statuses"][-1])
        self.assertEqual(r["lookups"], [])
        # Existing fixtures: default and explicit fold=False agree, scores and reasons.
        fixtures = [
            [f("README.md"), f("tests/test_x.py")],
            [f("app/views.py")],
            [f("app/migrations/0002.py")],
            [f(".github/risk-gate/risk-policy.toml")],
            [f("AGENTS.md")],
            [f("src/CLAUDE.md")],
            [f(".claude/settings.json")],
            [f(".mcp.json")],
            [f("accounts/auth.py")],
            [f("pyproject.toml")],
            [f("app/a.py", add=POLICY["limits"]["small_diff_lines"]), f("app/b.py", add=1)],
            [f("tests/test_x.py", add=0, rem=20)],
            [{**f("archive/ci.txt"), "previous": ".github/workflows/ci.yml"}],
            [{**f("unittest"), "link": "symlink or submodule"}],
            [f("CHANGELOG.md"), f("docs/user/install.md", add=12)],
            [f("docs/user/session-timeout.md")],
            [f(".opencode/agent/lead.md")],
        ]
        for files in fixtures:
            with self.subTest(files=[x["path"] for x in files]):
                self.assertEqual(g.rate_files(files, POLICY), g.rate_files(files, POLICY, fold=False))

    def test_zero_change_files_are_not_exempt(self):  # AC20
        for files in (
            [f("CHANGELOG.md", add=0, rem=0)],
            [f("docs/user/a.md", add=0, rem=0)],
            [{**f("CHANGELOG.md", add=0, rem=0), "previous": None}],  # main() passes None
            [f("CHANGELOG.md"), f("docs/user/a.md", add=0, rem=0)],
            [f("docs/user/a.md", add=0, rem=0), f("CHANGELOG.md")],
        ):
            with self.subTest(files=[(x["path"], x["additions"], x["deletions"]) for x in files]):
                self.assertEqual(g.rate_files(files, NR)[0], ZERO, "fixture: floor zero")
                self.assertIs(g.exempt_from_rating(files, NR), False)
                result = nr_lane(files)
                self.assertEqual(result[0], "review")
                self.assertIn(MISSING, result[3])
                self.assertNotIn(REASON, result[2])
        many = [f(f"docs/user/{i}.md", add=0, rem=0) for i in range(g.MAX_FILES - 1)]
        self.assertEqual(g.rate_files(many, NR)[0], ZERO, "fixture: below MAX_FILES, 0 lines")
        self.assertIs(g.exempt_from_rating(many, NR), False)
        self.assertEqual(nr_lane(many)[0], "review")
        # Controls: one changed line is enough. (0/0 renames: security round 2, see below.)
        for files in (
            [f("CHANGELOG.md", add=1, rem=0)],
            [f("CHANGELOG.md", add=0, rem=1)],
        ):
            with self.subTest(files=[(x["path"], x.get("previous")) for x in files], exempt=True):
                self.assertIs(g.exempt_from_rating(files, NR), True)
                self.assertEqual(nr_lane(files)[0], "auto")
        # A pure rename still needs both names on the list.
        out = {**moved("CHANGELOG.md", "docs/specs/x/spec.md"), "additions": 0, "deletions": 0}
        self.assertIs(g.exempt_from_rating([out], NR), False)


class AmendmentMain(unittest.TestCase):
    DOCS = ["CHANGELOG.md", "docs/user/install.md"]

    def assert_exempt_auto(self, r):
        self.assertEqual(len(r["lookups"]), 1, "exempt PR without rating: look up author")
        self.assertIn("description=auto R0 S0 U0", r["statuses"][-1])
        self.assertIn("--auto", r["merges"][-1])
        self.assertIn(f"- {REASON}", r["cards"][-1].splitlines())

    def test_main_malformed_rating_comment_disables_the_exemption(self):  # AC19
        malformed = {
            "regression: 4": rating(regression=4),
            "regression: n/a": rating(regression="n/a"),
            "no user line": drop_line(rating(), "user"),
            "newest of two is malformed": None,  # set below
        }
        malformed["newest of two is malformed"] = [rating(), rating(regression=4)]
        for label, comments in malformed.items():
            comments = comments if isinstance(comments, list) else [comments]
            self.assertIsNone(g.parse_rating(comments, SHA, ["owner"]), f"fixture: {label}")
            with self.subTest(case=label):
                r = run_gate(self.DOCS, comments)
                self.assertEqual(r["lookups"], [], "a trusted marker: no author lookup needed")
                self.assertIn("state=pending", r["statuses"][0])
                self.assertIn("description=review R0 S0 U0", r["statuses"][-1])
                self.assertFalse(any("--auto" in m for m in r["merges"]))
                self.assertIn(f"- {MISSING}", r["cards"][-1].splitlines())
                self.assertNotIn(REASON, r["cards"][-1])
                dry = run_gate(self.DOCS, comments, env={"RISK_GATE_DRY_RUN": "1"})
                self.assertTrue(dry["out"].startswith("review R0 S0 U0"), dry["out"])
                self.assertNotIn(REASON, dry["out"])
        with self.subTest(case="CHANGES"):
            r = run_gate(self.DOCS, [rating(verdict="CHANGES")])
            self.assertEqual(r["lookups"], [])
            self.assertIn("description=review R0 S0 U0", r["statuses"][-1])
            self.assertFalse(any("--auto" in m for m in r["merges"]))
            self.assertIn("- review verdict is CHANGES, not APPROVE", r["cards"][-1].splitlines())
            self.assertNotIn(REASON, r["cards"][-1])
            self.assertNotIn(MISSING, r["cards"][-1])
        with self.subTest(case="APPROVE"):  # the rating path, as today
            r = run_gate(self.DOCS, [rating()])
            self.assertEqual(r["lookups"], [])
            self.assertIn("description=auto R0 S0 U0", r["statuses"][-1])
            self.assertIn("--auto", r["merges"][-1])
            self.assertNotIn(REASON, r["cards"][-1])
        # Marker comments that do not count leave the exemption in place.
        other_sha = "b" * 40
        shifted = rating(regression=4)
        shifted["body"] = "\n" + shifted["body"]
        for label, comments in {
            "other login, malformed": [rating(login="stranger", regression=4)],
            "other login, CHANGES": [rating(login="stranger", verdict="CHANGES")],
            "other SHA, malformed": [rating(sha=other_sha, regression=4)],
            "other SHA, CHANGES": [rating(sha=other_sha, verdict="CHANGES")],
            "marker not at character 0": [shifted],
        }.items():
            with self.subTest(case=label):
                self.assert_exempt_auto(run_gate(self.DOCS, comments))


# WP1 security round 2 amendment (spec 4.2 step 4 and `canonical`, 4.6, AC7, AC14, AC18, AC20).

MORE_INSTRUCTION_FILES = ["AGENT.md", "WARP.md", "CRUSH.md", "CRUSH.local.md", "QWEN.md"]
NON_ASCII = [
    "docs/user/AGENTſ.md",  # U+017F LATIN SMALL LETTER LONG S casefolds to "s"
    "docs/user/AGENTſ.override.md",
    "docs/user/café.md",  # otherwise harmless: a non-ASCII doc name still needs a rating
]


class AmendmentRound2(unittest.TestCase):
    def test_non_ascii_names_are_not_exempt(self):  # AC7, AC18
        project = {**POLICY, "no_rating": {"paths": ["/docs/user/*.md"]}}
        for name in NON_ASCII:
            self.assertTrue(g.matches(name, "/docs/user/*.md"), f"fixture: {name} on the list")
            for policy, label in ((NR, "shipped"), (project, "project")):
                for files in (
                    [f(name)],
                    [f("CHANGELOG.md"), f(name)],
                    [moved("CHANGELOG.md", name)],
                    [moved(name, "CHANGELOG.md")],
                    [moved("docs/user/a.md", name), f("CHANGELOG.md")],
                ):
                    with self.subTest(files=[(x["path"], x.get("previous")) for x in files], p=label):
                        self.assertIs(g.exempt_from_rating(files, policy), False)
                        result = nr_lane(files, policy=policy)
                        self.assertNotEqual(result[0], "auto")
                        self.assertIn(MISSING, result[3])
                        self.assertNotIn(REASON, result[2])
        for path in ("CHANGELOG.md", "docs/user/a.md"):
            for policy, label in ((NR, "shipped"), (project, "project")):
                if label == "project" and path == "CHANGELOG.md":
                    continue  # not on the project list
                with self.subTest(path=path, p=label, exempt=True):
                    self.assertIs(g.exempt_from_rating([f(path)], policy), True)
                    self.assertEqual(nr_lane([f(path)], policy=policy)[0], "auto")

    def test_zero_change_renames_are_not_exempt(self):  # AC20 (amended)
        zero = {**moved("CHANGELOG.md", "docs/user/a.md"), "additions": 0, "deletions": 0}
        zero_back = {**moved("docs/user/a.md", "CHANGELOG.md"), "additions": 0, "deletions": 0}
        for files in (
            [zero],
            [zero_back],
            [zero, f("docs/user/b.md")],
            [f("docs/user/b.md"), zero],
        ):
            with self.subTest(files=[(x["path"], x.get("previous"), x["additions"]) for x in files]):
                self.assertEqual(g.rate_files(files, NR)[0], ZERO, "fixture: floor zero")
                self.assertIs(g.exempt_from_rating(files, NR), False)
                result = nr_lane(files)
                self.assertEqual(result[0], "review")
                self.assertIn(MISSING, result[3])
                self.assertNotIn(REASON, result[2])
        for files in (
            [moved("CHANGELOG.md", "docs/user/a.md")],
            [{**moved("CHANGELOG.md", "docs/user/a.md"), "additions": 0, "deletions": 1}],
            [moved("docs/user/b.md", "docs/user/a.md"), f("CHANGELOG.md")],
        ):
            with self.subTest(files=[(x["path"], x.get("previous")) for x in files], exempt=True):
                self.assertIs(g.exempt_from_rating(files, NR), True)
                self.assertEqual(nr_lane(files)[0], "auto")

    def test_more_agent_instruction_files_block(self):  # AC14 (4.6, security round 2)
        rule = next(r for r in POLICY["rule"] if r.get("reason") == "CI, deploy, gate or agent config")
        for name in MORE_INSTRUCTION_FILES:
            with self.subTest(pattern=name):
                self.assertIn(f"*/{name}", rule["paths"])
            for path in (name, f"docs/user/{name}", f"a/b/{name}"):
                for comments in ([rating()], []):
                    with self.subTest(path=path, rated=bool(comments)):
                        self.assertEqual(lane([f(path)], comments)[0], "block")
                        self.assertIs(g.exempt_from_rating([f(path)], NR), False)
                        self.assertEqual(nr_lane([f(path)], comments)[0], "block")
        for path in ("docs/user/qwen.md", "docs/user/crush.local.md"):
            with self.subTest(path=path, fold=True):
                self.assertEqual(g.rate_files([f(path)], POLICY, fold=True)[0]["regression"], 3)
                self.assertIs(g.exempt_from_rating([f(path)], NR), False)
                self.assertNotEqual(nr_lane([f(path)])[0], "auto")

    def test_rate_files_default_does_not_coerce_patterns(self):  # AC18: fold=False as at base
        bad = {**NR, "rule": POLICY["rule"] + [{"reason": "bad", "paths": [3], "regression": 0}]}
        # At base (9c48277) matches(path, 3) calls 3.startswith: AttributeError.
        with self.assertRaises(AttributeError):
            g.rate_files([f("a.py")], bad)
        with self.assertRaises(AttributeError):
            g.rate_files([f("a.py")], bad, fold=False)
        try:
            exempt = g.exempt_from_rating([f("CHANGELOG.md")], bad)
        except Exception:
            exempt = False  # raising fails closed too
        self.assertIs(exempt, False)


# FU5 (docs/specs/011-fu5-gate-block-rules/spec.md): opencode.json, .cursor/, case-folded floor.

AGENT_RULE = "CI, deploy, gate or agent config"
# Round 1 review added the legacy Cursor files: instructions and the access ignore list.
FU5_PATTERNS = ("*/opencode.json", "*/opencode.jsonc", "*/.cursor/*", "*/.cursorrules", "*/.cursorignore")
FU5_PATHS = ("opencode.json", "opencode.jsonc", "sub/opencode.json", ".cursor/rules/x.mdc",
             ".cursor/mcp.json", ".cursorrules", "sub/.cursorrules", ".cursorignore")
FU5_CASE_VARIANTS = (  # (variant, original)
    (".OpenCode/x", ".opencode/x"),
    (".Claude/settings.json", ".claude/settings.json"),
    (".AGENTS/skills/x/SKILL.md", ".agents/skills/x/SKILL.md"),
    (".Codex/x", ".codex/x"),
    ("Opencode.JSON", "opencode.json"),
    (".CURSOR/rules/x.mdc", ".cursor/rules/x.mdc"),
    ("CLAUDE.MD", "CLAUDE.md"),
    ("docs/Agents.md", "docs/AGENTS.md"),
    (".GitHub/workflows/ci.yml", ".github/workflows/ci.yml"),
)
NEVER_LOWER = ("app/views.py", "README.md", "docs/x.md", "tests/test_x.py", "README.MD",
               "Docs/x.py", "Tests/x.py", "app/Auth/x.py", "package.json", "PACKAGE.JSON")


def agent_rule(policy=POLICY):
    return next(r for r in policy["rule"] if r.get("reason") == AGENT_RULE)


def floor_lane(files, comments=()):
    r = g.parse_rating(list(comments), SHA, ["owner"])
    return g.decide(*g.floor(files, POLICY), r, POLICY)


class FoldedFloor(unittest.TestCase):
    def assertFloorShape(self, files):
        """floor = per-dimension max of both passes; each reason is a pass reason at that level."""
        scores, reasons = g.floor(files, POLICY)
        plain, folded = g.rate_files(files, POLICY), g.rate_files(files, POLICY, fold=True)
        self.assertEqual(scores, {d: max(plain[0][d], folded[0][d]) for d in g.DIMS})
        self.assertEqual(len(reasons), len(set(reasons)), "reasons deduplicated")
        for line in reasons:
            self.assertIn(line, plain[1] + folded[1])
            dim, level = line.split(":")[0].split()
            self.assertEqual(int(level), scores[dim], f"lower level shown: {line}")
        for dim in g.DIMS:
            if scores[dim]:
                self.assertTrue(any(r.startswith(f"{dim} {scores[dim]}: ") for r in reasons), dim)
        return scores, reasons

    def test_opencode_and_cursor_config_block(self):  # AC1
        for pattern in FU5_PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, agent_rule()["paths"])
        before = {**POLICY, "rule": [
            {**r, "paths": [p for p in r["paths"] if p not in FU5_PATTERNS]} for r in POLICY["rule"]
        ]}
        for path in FU5_PATHS:
            with self.subTest(path=path):
                self.assertEqual(g.rate_files([f(path)], before)[0]["regression"], 1, "fixture")
                scores, reasons = self.assertFloorShape([f(path)])
                self.assertEqual(scores["regression"], 3)
                self.assertIn(f"regression 3: {path} ({AGENT_RULE})", reasons)
                for comments in ([rating()], []):
                    self.assertEqual(floor_lane([f(path)], comments)[0], "block")

    def test_case_variants_rate_like_the_original(self):  # AC2
        for variant, original in FU5_CASE_VARIANTS:
            with self.subTest(path=variant):
                scores, reasons = self.assertFloorShape([f(variant)])
                self.assertEqual(scores, g.floor([f(original)], POLICY)[0])
                self.assertEqual(scores["regression"], 3)
                self.assertIn(f"regression 3: {variant} ({AGENT_RULE})", reasons)
                self.assertEqual(floor_lane([f(variant)], [rating()])[0], "block")

    def test_floor_never_lowers_a_score(self):  # AC3
        for path in NEVER_LOWER:
            with self.subTest(path=path):
                scores, _ = self.assertFloorShape([f(path)])
                plain = g.rate_files([f(path)], POLICY)[0]
                for dim in g.DIMS:
                    self.assertGreaterEqual(scores[dim], plain[dim], dim)
        self.assertEqual(g.floor([f("README.MD")], POLICY)[0]["regression"], 1)
        self.assertEqual(g.floor([f("PACKAGE.JSON")], POLICY)[0]["security"], 2)
        self.assertEqual(g.floor([f("app/Auth/x.py")], POLICY)[0]["security"], 2)
        whole = [f(p) for p in NEVER_LOWER] + [f("tests/test_y.py", add=0, rem=3)]
        self.assertFloorShape(whole)

    def test_reasons_name_only_the_deciding_level(self):  # AC4
        cases = {
            # plain pass says "regression 1 (ordinary code)": a lower level, not shown
            "Opencode.JSON": [f"regression 3: Opencode.JSON ({AGENT_RULE})"],
            "PACKAGE.JSON": [
                "regression 2: PACKAGE.JSON (dependency change)",
                "security 2: PACKAGE.JSON (dependency change)",
            ],
            "CLAUDE.md": [f"regression 3: CLAUDE.md ({AGENT_RULE})"],  # both passes: once
            ".CURSOR/rules/x.mdc": [f"regression 3: .CURSOR/rules/x.mdc ({AGENT_RULE})"],
        }
        for path, want in cases.items():
            with self.subTest(path=path):
                self.assertCountEqual(self.assertFloorShape([f(path)])[1], want)
        big = [f("app/a.py", add=POLICY["limits"]["small_diff_lines"] + 1)]
        limit = POLICY["limits"]["small_diff_lines"]
        self.assertEqual(
            self.assertFloorShape(big)[1], [f"regression 2: {limit + 1} changed lines (limit {limit})"]
        )

    def test_renames_are_rated_under_both_names(self):  # AC4
        for path, previous, why in (
            ("archive/ci.txt", ".GitHub/workflows/ci.yml", ".GitHub/workflows/ci.yml"),
            ("app/x.py", "Opencode.json", "Opencode.json"),
            (".Cursor/rules/x.mdc", "app/x.py", ".Cursor/rules/x.mdc"),
        ):
            with self.subTest(path=path, previous=previous):
                scores, reasons = self.assertFloorShape([moved(path, previous)])
                self.assertEqual(scores["regression"], 3)
                self.assertIn(f"regression 3: {why} ({AGENT_RULE})", reasons)


class FoldedFloorMain(unittest.TestCase):
    def test_main_blocks_case_variants_and_new_agent_config(self):  # AC5, AC1
        for path in (".Claude/x", "opencode.json", ".cursor/mcp.json"):
            for comments in ([rating()], []):
                with self.subTest(path=path, rated=bool(comments)):
                    dry = run_gate([path], comments, env={"RISK_GATE_DRY_RUN": "1"})
                    self.assertTrue(dry["out"].startswith("block R3"), dry["out"])
                    self.assertIn(f"  regression 3: {path} ({AGENT_RULE})\n", dry["out"])
                    self.assertEqual((dry["statuses"], dry["merges"], dry["cards"]), ([], [], []))
                    r = run_gate([path], comments)
                    self.assertIn("state=failure", r["statuses"][-1])
                    self.assertIn("description=block R3 S0 U0", r["statuses"][-1])
                    self.assertFalse(any("--auto" in m for m in r["merges"]))


DRY = {"RISK_GATE_DRY_RUN": "1"}
FORGED_NAME = "app/AUTH/x.py\n- security 0: forged"  # spec 014 AC2
# A rating is split on line breaks, so its fields carry only non-breaking controls (spec 014).
RATING_CTRL = "a\x1b[2J b\x1b]0;title\x07 c\td\x00e\x7ff\x9bg"
NAME_CTRL = "app/AUTH/a\x1b[2J b\x1b]0;title\x07 c\rd\te\x00f\x7fg\x85h i\x9bj.py"


def dry_counted(files, comments=()):
    seen = []
    real = g.decide

    def spy(*a, **kw):
        seen.append(real(*a, **kw))
        return seen[-1]

    with mock.patch.object(g, "decide", spy):
        r = run_gate(files, comments, env=DRY)
    [(_, _, reasons, blockers)] = seen
    return r["out"], reasons, blockers


class DryRunSanitise(unittest.TestCase):
    """Spec 014 FU8: the dry run prints one line per reason, no control characters."""

    def test_ordinary_output_is_byte_identical(self):  # AC1
        r = run_gate(
            ["app/f1.py", "app/AUTH/x.py"],
            [rating(user=2, disputed="F1, F2", security_pass="clean")],
            env=DRY,
        )
        self.assertEqual(
            r["out"],
            "review R1 S2 U2 head aaaaaaaaaaaa\n"
            "  regression 1: app/f1.py (ordinary code)\n"
            "  regression 1: app/AUTH/x.py (ordinary code)\n"
            "  regression 1: app/AUTH/x.py (auth, permissions, sessions or secrets)\n"
            "  security 2: app/AUTH/x.py (auth, permissions, sessions or secrets)\n"
            "  user 2: reviewer: ok\n"
            "  not auto: disputed findings: F1, F2\n",
        )
        self.assertEqual((r["statuses"], r["merges"], r["cards"]), ([], [], []))

    def test_forged_file_name_is_one_line(self):  # AC2
        out, _, _ = dry_counted([FORGED_NAME], [rating()])
        lines = out.split("\n")
        self.assertFalse(
            [ln for ln in lines if ln.lstrip().startswith(("- security 0", "security 0: forged"))],
            out,
        )
        self.assertEqual(
            out,
            "review R1 S2 U0 head aaaaaaaaaaaa\n"
            "  regression 1: app/AUTH/x.py - security 0: forged (ordinary code)\n"
            "  regression 1: app/AUTH/x.py - security 0: forged"
            " (auth, permissions, sessions or secrets)\n"
            "  security 2: app/AUTH/x.py - security 0: forged"
            " (auth, permissions, sessions or secrets)\n"
            "  not auto: security pass is not clean\n",  # rating() has security_pass n/a (lead fix)
        )

    def test_escape_sequences_become_spaces(self):  # AC3
        name_shown = "".join(c if c.isprintable() else " " for c in NAME_CTRL)
        ctrl_shown = "".join(c if c.isprintable() else " " for c in RATING_CTRL)
        self.assertEqual(name_shown, "app/AUTH/a [2J b ]0;title  c d e f g h i j.py")
        self.assertEqual(ctrl_shown, "a [2J b ]0;title  c d e f g")
        out, _, _ = dry_counted(
            [NAME_CTRL],
            [rating(user=2, reasons=RATING_CTRL, disputed=RATING_CTRL)],
        )
        self.assertEqual([c for c in out if c != "\n" and not c.isprintable()], [], repr(out))
        for bad in ("\x1b", "\x07", "\r", "\x00", "\x7f", "\x85", " ", "\x9b", "\t"):
            self.assertNotIn(bad, out)
        lines = out.split("\n")
        self.assertIn(f"  security 2: {name_shown} (auth, permissions, sessions or secrets)", lines)
        self.assertIn(f"  user 2: reviewer: {ctrl_shown}", lines)
        self.assertIn(f"  not auto: disputed findings: {ctrl_shown}", lines)
        self.assertTrue(out.startswith("review R1 S2 U2 head aaaaaaaaaaaa\n"))

    def test_one_line_per_reason_and_blocker(self):  # AC4
        for files, comments in (
            ([FORGED_NAME], [rating()]),
            ([FORGED_NAME, NAME_CTRL], [rating(regression=2, user=2, reasons=RATING_CTRL)]),
            ([NAME_CTRL], [rating(user=2, reasons=RATING_CTRL, disputed=RATING_CTRL)]),
            ([NAME_CTRL, "app/f1.py"], []),  # no rating: blockers only from the gate
        ):
            with self.subTest(files=files, rated=bool(comments)):
                out, reasons, blockers = dry_counted(files, comments)
                self.assertTrue(out.endswith("\n"))
                self.assertEqual(len(out.split("\n")) - 1, 1 + len(reasons) + len(blockers), out)
                body = out.split("\n")[1:-1]
                self.assertEqual(len(body), len(reasons) + len(blockers))
                self.assertTrue(all(ln.startswith("  ") for ln in body), out)
                self.assertEqual(
                    sum(ln.startswith("  not auto: ") for ln in body), len(blockers), out
                )


if __name__ == "__main__":
    unittest.main()
