from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from glabfut.client import GitLabProfileClient, _q_user_issues, _q_user_merge_requests, _q_user_projects
from glabfut.signals import profile_to_signals
import main
from main import _assign_lineup, _build_lineup_response, _build_user_bundle, _cache_key, _duel_result, get_card


def _card(position: str, overall: int, pac: int, sho: int, pas: int, dri: int, defe: int, phy: int) -> dict:
    return {
        "position": position,
        "overall": overall,
        "stats": {"pac": pac, "sho": sho, "pas": pas, "dri": dri, "def": defe, "phy": phy},
    }


class QueryBuilderTests(unittest.TestCase):
    def test_cursor_args_exist_for_project_queries(self) -> None:
        query, path = _q_user_projects("contributed")
        self.assertIn("$after", query)
        self.assertIn("$first", query)
        self.assertEqual(path, ["user", "contributedProjects"])
        self.assertNotIn("repository", query)
        self.assertNotIn("languages", query)

    def test_cursor_args_exist_for_merge_request_queries(self) -> None:
        query, path = _q_user_merge_requests("authored")
        self.assertIn("$after", query)
        self.assertIn("$first", query)
        self.assertEqual(path, ["user", "authoredMergeRequests"])

    def test_cursor_args_exist_for_issue_queries(self) -> None:
        query, path = _q_user_issues("assigned")
        self.assertIn("$after", query)
        self.assertIn("$first", query)
        self.assertEqual(path, ["issues"])
        self.assertIn("assigneeUsernames", query)
        self.assertIn("projectId", query)
        self.assertNotIn("assignedIssues", query)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_client_disables_safe_mode_compression(self) -> None:
        from glabfut import client as client_module

        original_client_class = client_module.Client

        class FakeClient:
            def __init__(self, url: str, token: str, **kwargs):
                self.url = url
                self.token = token
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

        client_module.Client = FakeClient
        try:
            client = GitLabProfileClient("https://gitlab.example.com", "token")
            gl = await client._get_client()
        finally:
            client_module.Client = original_client_class

        self.assertEqual(gl.url, "https://gitlab.example.com")
        self.assertFalse(gl.kwargs["safe_mode"])
        self.assertFalse(gl.kwargs["enable_compression"])

    async def test_fetch_user_events_uses_supplied_user_id(self) -> None:
        client = GitLabProfileClient("https://gitlab.example.com", "token")
        client.fetch_user_profile = AsyncMock(side_effect=AssertionError("profile should not be fetched"))
        client._rest_paginate = AsyncMock(return_value=[{"id": 1}])

        events = await client.fetch_user_events("alice", user_id=123)

        self.assertEqual(events, [{"id": 1}])
        client._rest_paginate.assert_awaited_once()

    async def test_fetch_user_projects_uses_streamed_connections(self) -> None:
        client = GitLabProfileClient("https://gitlab.example.com", "token")
        client._gql_stream = AsyncMock(
            side_effect=[
                [{"id": "gid://gitlab/Project/1", "fullPath": "oss/lib", "name": "lib", "repository": {"languages": {"nodes": []}}}],
                [{"id": "gid://gitlab/Project/2", "fullPath": "group/app", "name": "app", "repository": {"languages": {"nodes": []}}}],
            ]
        )
        client._fetch_project_languages = AsyncMock(side_effect=lambda project: project)

        projects = await client.fetch_user_projects("alice")

        self.assertEqual(len(projects), 2)
        self.assertEqual(client._gql_stream.await_count, 2)

    async def test_fetch_user_issues_rest_uses_numeric_ids(self) -> None:
        client = GitLabProfileClient("https://gitlab.example.com", "token")
        client._rest_paginate = AsyncMock(
            return_value=[
                {"id": 10, "iid": 7, "project_id": 99, "state": "opened"},
            ]
        )

        issues = await client._fetch_user_issues_rest(123, "authored")

        self.assertEqual(issues[0]["project_id"], 99)
        self.assertEqual(issues[0]["role"], "authored")
        client._rest_paginate.assert_awaited_once_with("/issues", {"state": "all", "author_id": 123})

    async def test_fetch_user_commit_stats_uses_rest_get_for_contributors(self) -> None:
        client = GitLabProfileClient("https://gitlab.example.com", "token")
        client._rest_get = AsyncMock(
            return_value=[
                {"name": "Alice Example", "email": "alice@example.com", "commits": 14},
            ]
        )
        client._rest_paginate = AsyncMock(side_effect=AssertionError("contributors should not use paginate"))

        stats = await client.fetch_user_commit_stats(
            "alice",
            {"username": "alice", "name": "Alice Example", "publicEmail": "alice@example.com"},
            [{"id": 99, "_contributed": True}],
            [],
        )

        self.assertEqual(stats["lifetime_commits"], 14)
        self.assertEqual(stats["commits_to_others"], 14)
        client._rest_get.assert_awaited_once_with("/projects/99/repository/contributors")

    async def test_fetch_user_commit_stats_ignores_non_list_contributors(self) -> None:
        client = GitLabProfileClient("https://gitlab.example.com", "token")
        client._rest_get = AsyncMock(return_value={"error": "bad response"})

        stats = await client.fetch_user_commit_stats(
            "alice",
            {"username": "alice"},
            [{"id": 99, "_contributed": True}],
            [],
        )

        self.assertEqual(stats["recent_commits"], 0)
        self.assertEqual(stats["lifetime_commits"], 0)
        self.assertEqual(stats["commits_to_others"], 0)


class SignalsTests(unittest.TestCase):
    def test_profile_to_signals_uses_reviewer_and_note_authorship(self) -> None:
        profile = {
            "name": "Alice Example",
            "username": "alice",
            "avatarUrl": "https://example.com/a.png",
            "createdAt": "2020-01-01T00:00:00Z",
            "followers": {"totalCount": 12},
        }
        projects = [
            {
                "id": 1,
                "fullPath": "group/app",
                "repository": {"languages": {"nodes": [{"name": "Python", "share": 75.0}]}},
                "starCount": 4,
            },
            {
                "id": 2,
                "fullPath": "oss/lib",
                "repository": {"languages": {"nodes": [{"name": "Go", "share": 25.0}]}},
                "starCount": 2,
            },
        ]
        mrs = [
            {
                "state": "merged",
                "role": "assigned",
                "reviewers": {"nodes": [{"username": "alice", "name": "Alice Example"}]},
                "notes": [
                    {"system": False, "author": {"username": "alice"}},
                    {"system": False, "author": {"username": "bob"}},
                ],
            },
            {
                "state": "opened",
                "role": "authored",
                "reviewers": {"nodes": [{"username": "other"}]},
                "notes": [{"system": False, "author": {"username": "bob"}}],
            },
        ]
        issues = [
            {"role": "authored"},
            {"role": "assigned"},
        ]
        signals = profile_to_signals(
            username="alice",
            profile=profile,
            events=[],
            projects=projects,
            mrs=mrs,
            issues=issues,
            commit_stats={"recent_commits": 21, "lifetime_commits": 144, "commits_to_others": 17},
        )

        assert signals is not None
        self.assertEqual(signals.recent_commits, 21)
        self.assertEqual(signals.lifetime_commits, 144)
        self.assertEqual(signals.commits_to_others, 17)
        self.assertEqual(signals.reviews_given, 1)
        self.assertEqual(signals.review_comments, 1)
        self.assertEqual(signals.issues_created, 1)
        self.assertEqual(signals.merged_mrs, 1)
        self.assertEqual(set(signals.languages.keys()), {"Python", "Go"})

    def test_profile_to_signals_falls_back_to_assigned_reviews_and_missing_commit_stats(self) -> None:
        profile = {
            "name": "Alice Example",
            "username": "alice",
            "createdAt": "2020-01-01T00:00:00Z",
        }
        mrs = [
            {
                "state": "opened",
                "role": "assigned",
                "reviewers": {"nodes": []},
                "notes": [{"system": False, "author": {"username": "alice"}}],
            }
        ]
        signals = profile_to_signals(
            username="alice",
            profile=profile,
            events=[],
            projects=[],
            mrs=mrs,
            issues=[],
            commit_stats=None,
        )

        assert signals is not None
        self.assertEqual(signals.recent_commits, 0)
        self.assertEqual(signals.lifetime_commits, 0)
        self.assertEqual(signals.reviews_given, 1)
        self.assertEqual(signals.review_comments, 1)


class BundleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        main.cache.clear()

    async def test_build_user_bundle_cached_response_keeps_project_paths_internal(self) -> None:
        payload = {"username": "alice", "overall": 80, "_project_paths": ["group/app"]}
        main.cache.set(_cache_key("alice", "https://gitlab.com", "token"), payload)

        bundle = await _build_user_bundle("alice", "https://gitlab.com", "token")

        self.assertEqual(bundle["projects"], ["group/app"])
        self.assertNotIn("_project_paths", bundle["response"])

    async def test_get_card_strips_internal_project_paths_from_public_response(self) -> None:
        payload = {"username": "alice", "overall": 80, "_project_paths": ["group/app"]}
        main.cache.set(_cache_key("alice", "https://gitlab.com", "token"), payload)

        response = await get_card(
            request=None,
            username="alice",
            gitlab_url="https://gitlab.com",
            x_gitlab_url=None,
            x_gitlab_token="token",
        )
        body = json.loads(response.body)

        self.assertEqual(body["username"], "alice")
        self.assertNotIn("_project_paths", body)

    async def test_build_user_bundle_fetch_path_returns_response_and_projects(self) -> None:
        original_client = main.GitLabProfileClient
        original_build_card = main.build_card
        original_fetch_user_data = main._fetch_user_data

        class FakeClient:
            def __init__(self, gitlab_url: str, token: str):
                self.gitlab_url = gitlab_url
                self.token = token

            async def fetch_user_profile(self, username: str):
                return {"id": 1, "username": username, "name": "Alice", "createdAt": "2020-01-01T00:00:00Z"}

            async def fetch_user_commit_stats(self, username: str, profile: dict, projects: list, events: list):
                return {"recent_commits": 12, "lifetime_commits": 42, "commits_to_others": 3}

            async def close(self):
                return None

        class FakeCard:
            username = "alice"
            name = "Alice"
            avatar_url = None
            bio = None
            location = None
            overall = 82
            base_ovr = 80
            position = "CAM"
            family = "Playmaker"
            tier = "Gold"
            finish = "gold"
            archetype = "Playmaker"
            archetype_blurb = "The midfield metronome."
            top_language = "Python"
            playstyles = ["Connector"]

            class stats:
                pac = 70
                sho = 68
                pas = 88
                dri = 85
                def_ = 52
                phy = 60

            class metrics:
                recent_commits = 12
                merged_mrs = 4
                followers = 2
                review_comments = 1
                languages = 2
                issues = 1
                reviews = 3
                lifetime_commits = 42
                active_years = 4
                projects = 2

        async def fake_fetch_user_data(client, username: str, profile: dict):
            return (
                [],
                [{"id": 1, "fullPath": "group/app"}, {"id": 2, "fullPath": "oss/lib"}],
                [],
                [],
            )

        try:
            main.GitLabProfileClient = FakeClient
            main.build_card = lambda signals: FakeCard()
            main._fetch_user_data = fake_fetch_user_data

            bundle = await _build_user_bundle("alice", "https://gitlab.com", "token")
        finally:
            main.GitLabProfileClient = original_client
            main.build_card = original_build_card
            main._fetch_user_data = original_fetch_user_data

        self.assertEqual(bundle["response"]["username"], "alice")
        self.assertEqual(bundle["projects"], ["group/app", "oss/lib"])


class GameplayTests(unittest.TestCase):
    def test_duel_result_scores_stats_and_overall(self) -> None:
        duel = _duel_result(
            {
                "username": "alice",
                "overall": 84,
                "stats": {"pac": 80, "sho": 70, "pas": 90, "dri": 75, "def": 60, "phy": 65},
            },
            {
                "username": "bob",
                "overall": 81,
                "stats": {"pac": 78, "sho": 88, "pas": 79, "dri": 75, "def": 63, "phy": 61},
            },
        )

        self.assertEqual(duel["stat_winners"]["pac"], "player1")
        self.assertEqual(duel["stat_winners"]["sho"], "player2")
        self.assertEqual(duel["stat_winners"]["dri"], "draw")
        self.assertEqual(duel["overall_winner"], "player1")
        self.assertEqual(duel["verdict"], "player1")

    def test_assign_lineup_prefers_similarity_when_no_exact_match(self) -> None:
        players = [
            {"username": "defender", "card": _card("CM", 80, 30, 25, 60, 30, 95, 92)},
            {"username": "forward", "card": _card("CAM", 82, 92, 90, 50, 82, 20, 70)},
            {"username": "mid1", "card": _card("CAM", 79, 70, 60, 84, 84, 30, 50)},
            {"username": "mid2", "card": _card("CM", 79, 68, 55, 85, 78, 50, 65)},
            {"username": "mid3", "card": _card("CM", 78, 64, 48, 80, 70, 60, 68)},
            {"username": "wide1", "card": _card("RW", 77, 90, 76, 70, 92, 22, 48)},
            {"username": "wide2", "card": _card("RW", 76, 88, 72, 69, 89, 20, 46)},
            {"username": "anchor", "card": _card("CDM", 78, 55, 42, 72, 52, 88, 84)},
            {"username": "cb1", "card": _card("CB", 80, 32, 30, 58, 28, 94, 93)},
            {"username": "cb2", "card": _card("CB", 79, 28, 26, 54, 25, 95, 94)},
            {"username": "extra", "card": _card("CAM", 75, 60, 52, 78, 76, 44, 58)},
        ]
        lineup = _assign_lineup(players)
        self.assertEqual(lineup[0]["slot"], "ST")
        self.assertNotEqual(lineup[0]["username"], "defender")
        self.assertEqual({player["username"] for player in lineup}, {player["username"] for player in players})

    def test_lineup_response_computes_chemistry_links(self) -> None:
        players = [
            {"username": "a", "project_paths": ["one", "two"], "card": _card("ST", 80, 90, 85, 60, 75, 25, 70)},
            {"username": "b", "project_paths": ["two"], "card": _card("RW", 79, 88, 75, 64, 89, 20, 58)},
            {"username": "c", "project_paths": ["three"], "card": _card("CAM", 78, 70, 68, 86, 84, 35, 55)},
            {"username": "d", "project_paths": ["three"], "card": _card("CM", 77, 68, 62, 82, 74, 55, 68)},
            {"username": "e", "project_paths": [], "card": _card("CM", 76, 65, 58, 80, 72, 52, 66)},
            {"username": "f", "project_paths": [], "card": _card("CDM", 75, 58, 44, 74, 54, 86, 82)},
            {"username": "g", "project_paths": [], "card": _card("CB", 74, 35, 28, 56, 26, 92, 91)},
            {"username": "h", "project_paths": [], "card": _card("CB", 74, 34, 27, 55, 25, 91, 90)},
            {"username": "i", "project_paths": [], "card": _card("RW", 73, 86, 70, 62, 88, 22, 50)},
            {"username": "j", "project_paths": [], "card": _card("CAM", 72, 67, 60, 79, 77, 40, 57)},
            {"username": "k", "project_paths": [], "card": _card("ST", 71, 84, 78, 58, 73, 24, 69)},
        ]
        response = _build_lineup_response(players)
        linked = [link for link in response["links"] if link["strength"] > 0]
        self.assertEqual(response["team_chemistry"], round(100 * len(linked) / len(response["links"])))
        self.assertTrue(any(set(link["shared_projects"]) == {"two"} for link in linked))
        self.assertTrue(any(set(link["shared_projects"]) == {"three"} for link in linked))

    def test_cache_key_scopes_by_token(self) -> None:
        self.assertNotEqual(
            _cache_key("alice", "https://gitlab.com", "token-one"),
            _cache_key("alice", "https://gitlab.com", "token-two"),
        )


if __name__ == "__main__":
    unittest.main()
