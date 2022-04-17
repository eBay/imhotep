import logging
from typing import Any, Dict, List, Optional, Union

from requests.models import Response
from six import string_types

from imhotep.http_client import BasicAuthRequester

from .reporter import Reporter

log = logging.getLogger(__name__)


class GitHubReporter(Reporter):
    def __init__(
        self, requester: BasicAuthRequester, domain: str, repo_name: str
    ) -> None:
        self._comments: List[Dict[str, Any]] = []
        self.domain = domain
        self.repo_name = repo_name
        self.requester = requester

    def clean_already_reported(
        self,
        comments: List[Dict[str, Any]],
        file_name: str,
        position: int,
        message: List[str],
    ) -> List[Union[str, Any]]:
        """
        message is potentially a list of messages to post. This is later
        converted into a string.
        """
        for comment in comments:
            if (
                comment["path"] == file_name
                and comment["position"] == position
                and comment["user"]["login"] == self.requester.username
            ):
                return [m for m in message if m not in comment["body"]]
        return message

    def get_comments(self, report_url: str) -> List[Dict[str, Any]]:
        if not self._comments:
            log.debug("PR Request: %s", report_url)
            result = self.requester.get(report_url)
            if result.status_code >= 400:
                log.error("Error requesting comments from github. %s", result.json())
                return self._comments
            self._comments = result.json()
        return self._comments

    def convert_message_to_string(self, message: List[str]) -> str:
        """Convert message from list to string for GitHub API."""
        final_message = ""
        for submessage in message:
            final_message += f"* {submessage}\n"
        return final_message

    def get_payload(
        self,
        comments_url: str,
        commit: Optional[str],
        commit_key: Optional[str],
        file_name: str,
        position: int,
        message: List[str],
    ) -> Optional[Dict[str, Union[str, int]]]:
        """
        Wraps a message (which is a string) into GitHub-understandable comment (which is a JSON object).
        It checks if there's already an identical comment on the PR. If there is, `None` is returned.
        """
        existing_comments = self.get_comments(comments_url)
        if isinstance(message, str):
            message = [message]
        message = self.clean_already_reported(
            existing_comments, file_name, position, message
        )
        if not message:
            log.debug("Message already reported")
            return None
        payload = {
            "body": self.convert_message_to_string(message),
            "path": file_name,  # relative file path
            "position": position,  # line index into the diff
        }
        if commit_key is not None and commit is not None:
            payload[commit_key] = commit
        return payload


class CommitReporter(GitHubReporter):
    def report_line(self, commit, file_name, position, message):
        report_url = "https://api.{}/repos/{}/commits/{}/comments".format(
            self.domain,
            self.repo_name,
            commit,
        )
        comments = self.get_comments(report_url)
        message = self.clean_already_reported(comments, file_name, position, message)
        payload = self.get_payload(
            report_url, commit, "commit_sha", file_name, position, message
        )
        if payload is None:
            return None
        log.debug("Commit Request: %s", report_url)
        log.debug("Commit Payload: %s", payload)
        result = self.requester.post(report_url, payload)
        if result.status_code >= 400:
            log.error("Error posting line to github. %s", result.json())
        return result


class PRReporter(GitHubReporter):
    """
    Comments on a PR by posting separate comments, rather than a review on the PR.
    See https://docs.github.com/en/rest/reference/pulls#create-a-review-comment-for-a-pull-request.
    """
    def __init__(
        self, requester: BasicAuthRequester, domain: str, repo_name: str, pr_number: str
    ) -> None:
        super().__init__(requester, domain, repo_name)
        self.pr_number = pr_number
        self.pr_comments_url = "https://api.{}/repos/{}/pulls/{}/comments".format(
            self.domain,
            self.repo_name,
            self.pr_number,
        )

    def report_line(
        self,
        commit: str,
        file_name: str,
        position: int,
        message: List[str],
    ) -> Optional[Response]:
        payload = self.get_payload(
            self.pr_comments_url, commit, "commit_id", file_name, position, message
        )
        if payload is None:
            return None
        log.debug("PR Request: %s", self.pr_comments_url)
        log.debug("PR Payload: %s", payload)
        result = self.requester.post(self.pr_comments_url, payload)
        if result.status_code >= 400:
            log.error("Error posting line to github. %s", result.json())
        return result

    def post_comment(self, message):
        """
        Comments on an issue, not on a particular line.
        """
        report_url = "https://api.{}/repos/{}/issues/{}/comments".format(
            self.domain,
            self.repo_name,
            self.pr_number,
        )
        result = self.requester.post(report_url, {"body": message})
        if result.status_code >= 400:
            log.error("Error posting comment to github. %s", result.json())
        return result


class PRReviewReporter(PRReporter):
    """
    Comments on a PR by posting a review on the PR, rather than separate comments.
    See https://docs.github.com/en/rest/reference/pulls#create-a-review-for-a-pull-request--parameters.
    """

    def __init__(
        self, requester: BasicAuthRequester, domain: str, repo_name: str, pr_number: str
    ) -> None:
        super().__init__(requester, domain, repo_name, pr_number)
        self.comments: List[Dict[str, Union[str, int]]] = list()

    def report_line(
        self,
        commit: str,
        file_name: str,
        position: int,
        message: List[str],
    ) -> Optional[Response]:
        payload = self.get_payload(
            self.pr_comments_url, None, None, file_name, position, message
        )
        if payload is None:
            return None
        self.comments.append(payload)

    def submit_review(self) -> Optional[Response]:
        """
        Submits review comments (stored in `self.comments`) to GitHub in one go as a single review.
        """
        self.pr_reviews_url = "https://api.{}/repos/{}/pulls/{}/reviews".format(
            self.domain,
            self.repo_name,
            self.pr_number,
        )
        if len(self.comments) == 0:
            return
        payload = {
            "body": "Imhotep detected {} potential problems with this PR.".format(
                len(self.comments)
            ),
            "event": "COMMENT",
            "comments": self.comments,
        }
        log.debug("PR Request: %s", self.pr_reviews_url)
        log.debug("PR Payload: %s", payload)
        result = self.requester.post(self.pr_reviews_url, payload)
        if result.status_code >= 400:
            log.error("Error posting review to github. %s", result.json())
        return result
