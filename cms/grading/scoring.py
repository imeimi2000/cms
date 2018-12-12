#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2015 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2013 Bernard Blackham <bernard@largestprime.net>
# Copyright © 2013-2014 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2016 Myungwoo Chun <mc.tamaki@gmail.com>
# Copyright © 2016 Amir Keivan Mohtashami <akmohtashami97@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
from future.builtins.disabled import *  # noqa
from future.builtins import *  # noqa
from six import iteritems, itervalues

from collections import namedtuple

from sqlalchemy.orm import joinedload

from cms.db import Submission
from cmscommon.constants import \
    SCORE_MODE_MAX, SCORE_MODE_MAX_SUBTASK, SCORE_MODE_MAX_TOKENED_LAST, SCORE_MODE_MAX_OTHER_USERS

import math

__all__ = [
    "compute_changes_for_dataset", "task_score",
]


SubmissionScoreDelta = namedtuple(
    'SubmissionScoreDelta',
    ['submission', 'old_score', 'new_score',
     'old_public_score', 'new_public_score',
     'old_ranking_score_details', 'new_ranking_score_details'])


def compute_changes_for_dataset(old_dataset, new_dataset):
    """This function will compute the differences expected when changing from
    one dataset to another.

    old_dataset (Dataset): the original dataset, typically the active one.
    new_dataset (Dataset): the dataset to compare against.

    returns (list): a list of tuples of SubmissionScoreDelta tuples
        where they differ. Those entries that do not differ will have
        None in the pair of respective tuple entries.

    """
    # If we are switching tasks, something has gone seriously wrong.
    if old_dataset.task is not new_dataset.task:
        raise ValueError(
            "Cannot compare datasets referring to different tasks.")

    task = old_dataset.task

    def compare(a, b):
        if a == b:
            return False, (None, None)
        else:
            return True, (a, b)

    # Construct query with all relevant fields to avoid roundtrips to the DB.
    submissions = \
        task.sa_session.query(Submission)\
            .filter(Submission.task == task)\
            .options(joinedload(Submission.participation))\
            .options(joinedload(Submission.token))\
            .options(joinedload(Submission.results)).all()

    ret = []
    for s in submissions:
        old = s.get_result(old_dataset)
        new = s.get_result(new_dataset)

        diff1, pair1 = compare(
            old.score if old is not None else None,
            new.score if new is not None else None)
        diff2, pair2 = compare(
            old.public_score if old is not None else None,
            new.public_score if new is not None else None)
        diff3, pair3 = compare(
            old.ranking_score_details if old is not None else None,
            new.ranking_score_details if new is not None else None)

        if diff1 or diff2 or diff3:
            ret.append(SubmissionScoreDelta(*(s,) + pair1 + pair2 + pair3))

    return ret


# Computing global scores (for ranking).

def task_score(participation, task, public=False, only_tokened=False):
    """Return the score of a contest's user on a task.

    participation (Participation): the user and contest for which to
        compute the score.
    task (Task): the task for which to compute the score.
    public (bool): if True, compute the public score (that is, the one
        discoverable looking only at the result of public testcases) instead
        of the full score.

    return ((float, bool)): the score of user on task, and True if not
        all submissions of the participation in the task have been scored.

    """
    # As this function is primarily used when generating a rankings table
    # (AWS's RankingHandler), we optimize for the case where we are generating
    # results for all users and all tasks. As such, for the following code to
    # be more efficient, the query that generated task and user should have
    # come from a joinedload with the submissions, tokens and
    # submission_results table. Doing so means that this function should incur
    # no exta database queries.

    if public and only_tokened:
        raise ValueError(
            "Public task score requested for tokened submissions. "
            "This is a programming error: users have access to all public "
            "scores")
    if task.score_mode == SCORE_MODE_MAX_OTHER_USERS:
	    return _task_score_max_other_users(
		    participation, task, public=public, only_tokened=only_tokened)

    submissions = [s for s in participation.submissions
                   if s.task is task and s.official]
    if len(submissions) == 0:
        return 0.0, False

    submissions_and_results = [
        (s, s.get_result(task.active_dataset))
        for s in sorted(submissions, key=lambda s: s.timestamp)]

    if public:
        score_details_tokened = [
            (sr.public_score if sr is not None and sr.scored() else None,
             sr.public_score_details
             if sr is not None and sr.scored() else None,
             s.tokened())
            for s, sr in submissions_and_results]
    else:
        score_details_tokened = [
            (sr.score if sr is not None and sr.scored() else None,
             sr.score_details if sr is not None and sr.scored() else None,
             s.tokened())
            for s, sr in submissions_and_results]

    if task.score_mode == SCORE_MODE_MAX:
        return _task_score_max(
            score_details_tokened, only_tokened=only_tokened)
    if task.score_mode == SCORE_MODE_MAX_SUBTASK:
        return _task_score_max_subtask(
            score_details_tokened, only_tokened=only_tokened)
    elif task.score_mode == SCORE_MODE_MAX_TOKENED_LAST:
        return _task_score_max_tokened_last(
            score_details_tokened, only_tokened=only_tokened)
    else:
        raise ValueError("Unknown score mode '%s'" % task.score_mode)


def _task_score_max_tokened_last(score_details_tokened,
                                 only_tokened):
    """Compute score using the "max tokened last" score mode.

    This was used in IOI 2010-2012. The score of a participant on a task is
    the maximum score amongst all tokened submissions and the last submission
    (not yet computed scores count as 0.0).

    submissions_and_results ([(Submission, SubmissionResult|None)]): list of
        all submissions and their results for the participant on the task (on
        the dataset of interest); result is None if not available (that is,
        if the submission has not been compiled).

    return ((float, bool)): (score, partial), same as task_score().

    """
    partial = False

    # The score of the last submission (if computed, otherwise 0.0). Note that
    # partial will be set to True in the next loop.
    last_score, _, last_tokened = score_details_tokened[-1]
    if last_score is None:
        last_score = 0.0
    if only_tokened and not last_tokened:
        last_score = 0.0

    # The maximum score amongst the tokened submissions (not yet computed
    # scores count as 0.0).
    max_tokened_score = 0.0
    for score, _, tokened in score_details_tokened:
        if score is not None:
            if tokened:
                max_tokened_score = max(max_tokened_score, score)
        else:
            partial = True

    return max(last_score, max_tokened_score), partial


def _task_score_max_subtask(score_details_tokened, only_tokened):
    """Compute score using the "max subtask" score mode.

    This is used in IOI since 2017-. The score of a participant on a task is
    the sum, over the subtasks, of the maximum score amongst all submissions
    for that subtask(not yet computed scores count as 0.0).

    If this score mode is selected, all tasks should be children of
    ScoreTypeGroup, or follow the same format for their score details. If
    this is not true, the score mode will work as if the task had a single
    subtask.

    submissions_and_results ([(Submission, SubmissionResult|None)]): list of
        all submissions and their results for the participant on the task (on
        the dataset of interest); result is None if not available (that is,
        if the submission has not been compiled).

    return ((float, bool)): (score, partial), same as task_score().

    """
    # The maximum scores for each subtasks (not yet computed scores count
    # as 0.0).
    max_scores = {}

    partial = False
    for score, details, tokened in score_details_tokened:
        if score is None:
            partial = True
            continue

        if only_tokened and not tokened:
            continue

        try:
            subtask_scores = dict(
                (subtask["idx"],
                 subtask["score_fraction"] * subtask["max_score"])
                for subtask in details)
        except Exception:
            subtask_scores = None

        if subtask_scores is None or len(subtask_scores) == 0:
            # Task's score type is not group, assume a single subtask.
            subtask_scores = {1: score}

        for idx, score in iteritems(subtask_scores):
            max_scores[idx] = max(max_scores.get(idx, 0.0), score)

    if max_scores is not None:
        return sum(itervalues(max_scores)), partial
    else:
        return 0.0, partial


def _task_score_max(score_details_tokened, only_tokened):
    """Compute score using the "max" score mode.

    This was used in IOI 2013-2016. The score of a participant on a task is
    the maximum score amongst all submissions (not yet computed scores count
    as 0.0).

    submissions_and_results ([(Submission, SubmissionResult|None)]): list of
        all submissions and their results for the participant on the task (on
        the dataset of interest); result is None if not available (that is,
        if the submission has not been compiled).

    return ((float, bool)): (score, partial), same as task_score().

    """
    partial = False
    max_score = 0.0

    for score, _, tokened in score_details_tokened:
        if score is not None:
            if only_tokened and not tokened:
                continue
            max_score = max(max_score, score)
        else:
            partial = True

    return max_score, partial

def _task_calc_first_score(contest, task, user_score):
    first_score = 0.0
    for p_iter in contest.participations:
        submissions = [s for s in p_iter.submissions if s.task is task and s.official]
        for s_iter in submissions:
            result = s_iter.get_result(task.active_dataset)
            if result is not None and result.scored():
                first_score = max(first_score, result.score)
    return first_score

def _participation_get_subtask_scores(participation, task, public=False, only_tokened=False):
    submissions = [s for s in participation.submissions
                   if s.task is task and s.official]
    if len(submissions) == 0:
        return None, False, None

    submissions_and_results = [
        (s, s.get_result(task.active_dataset))
        for s in submissions]

    if public:
        score_details_tokened = [
            (sr.public_score if sr is not None and sr.scored() else None,
             sr.public_score_details
             if sr is not None and sr.scored() else None,
             s.tokened())
            for s, sr in submissions_and_results]
    else:
        score_details_tokened = [
            (sr.score if sr is not None and sr.scored() else None,
             sr.score_details if sr is not None and sr.scored() else None,
             s.tokened())
            for s, sr in submissions_and_results]

    max_scores = {}
    full_scores = None

    partial = False
    for score, details, tokened in score_details_tokened:
        if score is None:
            partial = True
            continue

        if only_tokened and not tokened:
            continue

        try:
            subtask_scores = dict(
                (subtask["idx"],
                 subtask["score_fraction"])
                for subtask in details)
            if full_scores == None:
                full_scores = dict(
                    (subtask["idx"],
                     subtask["max_score"])
                    for subtask in details)
        except Exception:
            subtask_scores = None

        if subtask_scores is None or len(subtask_scores) == 0:
            # Task's score type is not group, assume a single subtask.
            subtask_scores = {1: score}

        for idx, score in iteritems(subtask_scores):
            max_scores[idx] = max(max_scores.get(idx, 0.0), score)
    return max_scores, partial, full_scores

def _get_subtask_score_by_rank(scores, max_score, user_score):
    if user_score <= 0.0:
        return 0.0
    scores.sort()
    rank = 0
    while rank < len(scores):
        if scores[len(scores) - rank - 1] <= user_score + 1e-9:
            break
        rank = rank + 1
    return max_score * (1.0 - rank / max(len(scores), 1.0))

def _task_score_max_other_users(participation, task, public, only_tokened):
    user_score, partial, _A = _participation_get_subtask_scores(participation, task, public, only_tokened)
    max_scores = None
    if user_score == None:
        return 0.0, partial

    subtask_rank = {}
    for p_iter in participation.contest.participations:
        if p_iter.hidden:
            continue
        subtask_scores, _A, _max_scores = _participation_get_subtask_scores(p_iter, task)
        if max_scores == None:
            max_scores = _max_scores
        if subtask_scores == None:
            continue
        for idx, score in iteritems(subtask_scores):
            if idx not in subtask_rank:
                subtask_rank[idx] = []
            if score <= 0.0:
                continue
            subtask_rank[idx].append(score)

    final_score = 0.0
    for idx, score in iteritems(user_score):
        final_score += _get_subtask_score_by_rank(subtask_rank.get(idx, []), max_scores.get(idx, 0.0), score)
    return final_score, partial
