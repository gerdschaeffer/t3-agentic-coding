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
            ".OpenCode/opencode.json": (
                {**ZERO, "regression": 1},
                ["regression 1: .OpenCode/opencode.json (ordinary code)"],
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
        r = run_gate(["docs/user/Claude.md"], [rating()])
        self.assertIn("description=auto R0 S0 U0", r["statuses"][-1])
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


if __name__ == "__main__":
    unittest.main()
