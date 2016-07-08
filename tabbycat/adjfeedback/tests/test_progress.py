from django.test import TestCase

from adjallocation.models import DebateAdjudicator
from adjfeedback.models import AdjudicatorFeedback
from draw.models import Debate, DebateTeam
from participants.models import Adjudicator, Institution, Speaker, Team
from results.models import BallotSubmission
from results.result import BallotSet
from tournaments.models import Round, Tournament
from venues.models import Venue

from ..progress import FeedbackExpectedSubmissionFromAdjudicatorTracker, FeedbackExpectedSubmissionFromTeamTracker
from ..progress import FeedbackProgressForAdjudicator, FeedbackProgressForTeam


class TestFeedbackProgress(TestCase):

    NUM_TEAMS = 6
    NUM_ADJS = 7
    NUM_VENUES = 3

    def setUp(self):
        self.t = Tournament.objects.create()
        for i in range(self.NUM_TEAMS):
            inst = Institution.objects.create(code=i, name=i)
            team = Team.objects.create(tournament=self.t, institution=inst, reference=i)
            for j in range(3):
                Speaker.objects.create(team=team, name="%d-%d" % (i, j))

        adjsinst = Institution.objects.create(code="Adjs", name="Adjudicators")
        for i in range(self.NUM_ADJS):
            Adjudicator.objects.create(tournament=self.t, institution=adjsinst, name=i)
        for i in range(self.NUM_VENUES):
            Venue.objects.create(name=i, priority=i)

        self.rd = Round.objects.create(tournament=self.t, seq=1, abbreviation="R1")

    def _team(self, t):
        return Team.objects.get(tournament=self.t, reference=t)

    def _adj(self, a):
        return Adjudicator.objects.get(tournament=self.t, name=a)

    def _dt(self, debate, t):
        return DebateTeam.objects.get(debate=debate, team=self._team(t))

    def _da(self, debate, a):
        return DebateAdjudicator.objects.get(debate=debate, adjudicator=self._adj(a))

    def _create_debate(self, teams, adjs, votes, trainees=[], venue=None):
        """Enters a debate into the database, using the teams and adjudicators specified.
        `votes` should be a string (or iterable of characters) indicating "a" for affirmative or
            "n" for negative, e.g. "ann" if the chair was rolled in a decision for the negative.
        The method will give the winning team all 76s and the losing team all 74s.
        The first adjudicator is the chair; the rest are panellists."""

        if venue is None:
            venue = Venue.objects.first()
        debate = Debate.objects.create(round=self.rd, venue=venue)

        aff, neg = teams
        aff_team = self._team(aff)
        DebateTeam.objects.create(debate=debate, team=aff_team, position=DebateTeam.POSITION_AFFIRMATIVE)
        neg_team = self._team(neg)
        DebateTeam.objects.create(debate=debate, team=neg_team, position=DebateTeam.POSITION_NEGATIVE)

        chair = self._adj(adjs[0])
        DebateAdjudicator.objects.create(debate=debate, adjudicator=chair,
                type=DebateAdjudicator.TYPE_CHAIR)
        for p in adjs[1:]:
            panellist = self._adj(p)
            DebateAdjudicator.objects.create(debate=debate, adjudicator=panellist,
                    type=DebateAdjudicator.TYPE_PANEL)
        for tr in trainees:
            trainee = self._adj(tr)
            DebateAdjudicator.objects.create(debate=debate, adjudicator=trainee,
                    type=DebateAdjudicator.TYPE_TRAINEE)

        ballotsub = BallotSubmission(debate=debate, submitter_type=BallotSubmission.SUBMITTER_TABROOM)
        ballotset = BallotSet(ballotsub)

        for t in teams:
            team = self._team(t)
            speakers = team.speaker_set.all()
            for pos, speaker in enumerate(speakers, start=1):
                ballotset.set_speaker(team, pos, speaker)
            ballotset.set_speaker(team, 4, speakers[0])

        for a, vote in zip(adjs, votes):
            adj = self._adj(a)
            if vote == 'a':
                teams = [aff_team, neg_team]
            elif vote == 'n':
                teams = [neg_team, aff_team]
            else:
                raise ValueError
            for team, score in zip(teams, (76, 74)):
                for pos in range(1, 4):
                    ballotset.set_score(adj, team, pos, score)
                ballotset.set_score(adj, team, 4, score / 2)

        ballotset.confirmed = True
        ballotset.save()

        return debate

    def _create_feedback(self, source, target):
        if isinstance(source, DebateTeam):
            source_kwargs = dict(source_team=source)
        else:
            source_kwargs = dict(source_adjudicator=source)
        target_adj = self._adj(target)
        return AdjudicatorFeedback.objects.create(confirmed=True, adjudicator=target_adj, score=3,
                **source_kwargs)

    # ==========================================================================
    # From team
    # ==========================================================================

    def assertExpectedFromTeamTracker(self, debate, t, expected, fulfilled, count, submissions, targets): # noqa
        tracker = FeedbackExpectedSubmissionFromTeamTracker(self._dt(debate, t))
        self.assertIs(tracker.expected, expected)
        self.assertIs(tracker.fulfilled, fulfilled)
        self.assertEqual(tracker.count, count)
        self.assertCountEqual(tracker.acceptable_submissions(), submissions)
        self.assertCountEqual(tracker.acceptable_targets(), [self._adj(a) for a in targets])

    def test_chair_oral_no_submission(self):
        debate = self._create_debate((0, 1), (0, 1, 2), "aan")
        for t in (0, 1):
            self.assertExpectedFromTeamTracker(debate, t, True, False, 0, [], [0])

    def test_chair_oral_good_submission(self):
        debate = self._create_debate((0, 1), (0, 1, 2), "aan")
        for t in (0, 1):
            feedback = self._create_feedback(self._dt(debate, t), 0)
            self.assertExpectedFromTeamTracker(debate, t, True, True, 1, [feedback], [0])

    def test_chair_oral_bad_submission(self):
        debate = self._create_debate((0, 1), (0, 1, 2), "aan")
        for t in (0, 1):
            self._create_feedback(self._dt(debate, t), 1)
            self.assertExpectedFromTeamTracker(debate, t, True, False, 0, [], [0])

    def test_chair_oral_multiple_submissions(self):
        debate = self._create_debate((0, 1), (0, 1, 2), "aan")
        for t in (0, 1):
            feedback1 = self._create_feedback(self._dt(debate, t), 0)
            self._create_feedback(self._dt(debate, t), 1)
            # The submission on adj 1 is irrelevant, so shouldn't appear at all.
            # (It should appear as "unexpected" in the FeedbackProgressForTeam.)
            self.assertExpectedFromTeamTracker(debate, t, True, True, 1, [feedback1], [0])

    def test_chair_rolled_no_submission(self):
        debate = self._create_debate((0, 1), (0, 1, 2), "ann")
        for t in (0, 1):
            self.assertExpectedFromTeamTracker(debate, t, True, False, 0, [], [1, 2])

    def test_chair_rolled_good_submission(self):
        debate = self._create_debate((0, 1), (0, 1, 2), "ann")
        for t in (0, 1):
            feedback = self._create_feedback(self._dt(debate, t), 1)
            self.assertExpectedFromTeamTracker(debate, t, True, True, 1, [feedback], [1, 2])

    def test_chair_rolled_bad_submission(self):
        debate = self._create_debate((0, 1), (0, 1, 2), "ann")
        for t in (0, 1):
            self._create_feedback(self._dt(debate, t), 0)
            self.assertExpectedFromTeamTracker(debate, t, True, False, 0, [], [1, 2])

    def test_chair_rolled_multiple_submissions(self):
        debate = self._create_debate((0, 1), (0, 1, 2), "ann")
        for t in (0, 1):
            feedback1 = self._create_feedback(self._dt(debate, t), 1)
            feedback2 = self._create_feedback(self._dt(debate, t), 2)
            self.assertExpectedFromTeamTracker(debate, t, True, False, 2, [feedback1, feedback2], [1, 2])

    def test_sole_adjudicator_no_submissions(self):
        debate = self._create_debate((0, 1), (0,), "n")
        for t in (0, 1):
            self.assertExpectedFromTeamTracker(debate, t, True, False, 0, [], [0])

    def test_sole_adjudicator_good_submission(self):
        debate = self._create_debate((0, 1), (0,), "n")
        for t in (0, 1):
            feedback = self._create_feedback(self._dt(debate, t), 0)
            self.assertExpectedFromTeamTracker(debate, t, True, True, 1, [feedback], [0])

    def test_sole_adjudicator_bad_submission(self):
        debate = self._create_debate((0, 1), (0,), "n")
        for t in (0, 1):
            self._create_feedback(self._dt(debate, t), 3)
            self.assertExpectedFromTeamTracker(debate, t, True, False, 0, [], [0])

    def test_sole_adjudicator_multiple_submissions(self):
        debate = self._create_debate((0, 1), (0,), "n")
        for t in (0, 1):
            feedback1 = self._create_feedback(self._dt(debate, t), 0)
            self._create_feedback(self._dt(debate, t), 3)
            self._create_feedback(self._dt(debate, t), 4)
            self.assertExpectedFromTeamTracker(debate, t, True, True, 1, [feedback1], [0])
            # The submissions on adjs 3 and 4 are irrelevant, so shouldn't appear at all.
            # (They should appear as "unexpected" in the FeedbackProgressForTeam.)

    # ==========================================================================
    # From adjudicator
    # ==========================================================================

    def assertExpectedFromAdjudicatorTracker(self, debate, source, target, expected, fulfilled, count, submissions): # noqa
        tracker = FeedbackExpectedSubmissionFromAdjudicatorTracker(self._da(debate, source), self._adj(target))
        self.assertIs(tracker.expected, expected)
        self.assertIs(tracker.fulfilled, fulfilled)
        self.assertEqual(tracker.count, count)
        self.assertCountEqual(tracker.acceptable_submissions(), submissions)
        self.assertCountEqual(tracker.acceptable_targets(), [self._adj(target)])

    def test_adj_on_adj_no_submission(self):
        debate = self._create_debate((0, 1), (0, 1, 2), "aan")
        for a in (1, 2):
            self.assertExpectedFromAdjudicatorTracker(debate, 0, a, True, False, 0, [])

    def test_adj_on_adj_good_submission(self):
        debate = self._create_debate((0, 1), (0, 1, 2), "aan")
        for a in (1, 2):
            feedback = self._create_feedback(self._da(debate, 0), a)
            self.assertExpectedFromAdjudicatorTracker(debate, 0, a, True, True, 1, [feedback])

    def test_adj_on_adj_bad_submission(self):
        debate = self._create_debate((0, 1), (0, 1, 2), "aan")
        for a in (1, 2):
            self._create_feedback(self._da(debate, 0), 4)
            self.assertExpectedFromAdjudicatorTracker(debate, 0, a, True, False, 0, [])

    def test_adj_on_adj_multiple_submission(self):
        debate = self._create_debate((0, 1), (0, 1, 2), "aan")
        for a in (1, 2):
            self._create_feedback(self._da(debate, 0), a)
            feedback2 = self._create_feedback(self._da(debate, 0), a)
            self.assertExpectedFromAdjudicatorTracker(debate, 0, a, True, True, 1, [feedback2])

    def test_adj_on_adj_trainees_not_submitted(self):
        debate = self._create_debate((0, 1), (0,), "n", trainees=[4])
        self.assertExpectedFromAdjudicatorTracker(debate, 0, 4, True, False, 0, [])

    def test_adj_on_adj_trainees_submitted(self):
        debate = self._create_debate((0, 1), (0, 1, 2), "nan", trainees=[4])
        feedback = self._create_feedback(self._da(debate, 0), 4)
        self.assertExpectedFromAdjudicatorTracker(debate, 0, 4, True, True, 1, [feedback])

    # ==========================================================================
    # Team progress
    # ==========================================================================

    def _create_team_progress_dataset(self, adj1, adj2, adj3):
        debate1 = self._create_debate((0, 1), (0, 1, 2), "nnn")
        debate2 = self._create_debate((0, 2), (3, 4, 5), "ann")
        debate3 = self._create_debate((0, 3), (6,), "a")
        if adj1 is not None:
            self._create_feedback(self._dt(debate1, 0), adj1)
        if adj2 is not None:
            self._create_feedback(self._dt(debate2, 0), adj2)
        if adj3 is not None:
            self._create_feedback(self._dt(debate3, 0), adj3)

    def assertTeamProgress(self, t, submitted, expected, fulfilled, unsubmitted, coverage): # noqa
        progress = FeedbackProgressForTeam(self._team(t))
        self.assertEqual(progress.num_submitted(), submitted)
        self.assertEqual(progress.num_expected(), expected)
        self.assertEqual(progress.num_fulfilled(), fulfilled)
        self.assertEqual(progress.num_unsubmitted(), unsubmitted)
        self.assertAlmostEqual(progress.coverage(), coverage)
        return progress

    def test_team_progress_all_good(self):
        self._create_team_progress_dataset(0, 4, 6)
        self.assertTeamProgress(0, 3, 3, 3, 0, 1.0)

    def test_team_progress_no_submissions(self):
        self._create_team_progress_dataset(None, None, None)
        self.assertTeamProgress(0, 0, 3, 0, 3, 0.0)

    def test_team_progress_no_debates(self):
        FeedbackProgressForTeam(self._team(4))
        self.assertTeamProgress(4, 0, 0, 0, 0, 1.0)

    def test_team_progress_missing_submission(self):
        self._create_team_progress_dataset(0, None, 6)
        self.assertTeamProgress(0, 2, 3, 2, 1, 2/3)

    def test_team_progress_wrong_target_on_unanimous(self):
        self._create_team_progress_dataset(2, 4, 6)
        progress = self.assertTeamProgress(0, 3, 3, 2, 1, 2/3)
        self.assertEqual(len(progress.unexpected_trackers()), 1)

    def test_team_progress_wrong_target_on_rolled_chair(self):
        self._create_team_progress_dataset(0, 3, 6)
        progress = self.assertTeamProgress(0, 3, 3, 2, 1, 2/3)
        self.assertEqual(len(progress.unexpected_trackers()), 1)

    def test_team_progress_unexpected(self):
        self._create_team_progress_dataset(5, 3, None)
        progress = self.assertTeamProgress(0, 2, 3, 0, 3, 0.0)
        self.assertEqual(len(progress.unexpected_trackers()), 2)

    # ==========================================================================
    # Adjudicator progress
    # ==========================================================================

    def _create_adjudicator_progress_dataset(self, adjs1, adjs2, adjs3):
        debate1 = self._create_debate((0, 1), (0, 1, 2), "nnn")
        debate2 = self._create_debate((2, 3), (3, 0, 4), "ann")
        debate3 = self._create_debate((4, 0), (0,), "a")
        for adj in adjs1:
            self._create_feedback(self._da(debate1, 0), adj)
        for adj in adjs2:
            self._create_feedback(self._da(debate2, 0), adj)
        for adj in adjs3:
            self._create_feedback(self._da(debate3, 0), adj)

    def assertAdjudicatorProgress(self, a, submitted, expected, fulfilled, unsubmitted, coverage): # noqa
        progress = FeedbackProgressForAdjudicator(self._adj(a))
        self.assertEqual(progress.num_submitted(), submitted)
        self.assertEqual(progress.num_expected(), expected)
        self.assertEqual(progress.num_fulfilled(), fulfilled)
        self.assertEqual(progress.num_unsubmitted(), unsubmitted)
        self.assertAlmostEqual(progress.coverage(), coverage)
        return progress

    def test_adjudicator_progress_all_good(self):
        self._create_adjudicator_progress_dataset([1, 2], [3], [])
        self.assertAdjudicatorProgress(0, 3, 3, 3, 0, 1.0)

    def test_adjudicator_progress_no_submissions(self):
        self._create_adjudicator_progress_dataset([], [], [])
        self.assertAdjudicatorProgress(0, 0, 3, 0, 3, 0.0)

    def test_adjudicator_progress_no_debates(self):
        FeedbackProgressForAdjudicator(self._adj(5))
        self.assertAdjudicatorProgress(5, 0, 0, 0, 0, 1.0)

    def test_adjudicator_progress_missing_submission(self):
        self._create_adjudicator_progress_dataset([1], [3], [])
        self.assertAdjudicatorProgress(0, 2, 3, 2, 1, 2/3)

    def test_adjudicator_progress_wrong_target(self):
        self._create_adjudicator_progress_dataset([1, 2], [4], [])
        progress = self.assertAdjudicatorProgress(0, 3, 3, 2, 1, 2/3)
        self.assertEqual(len(progress.unexpected_trackers()), 1)

    def test_adjudicator_progress_extra_target(self):
        self._create_adjudicator_progress_dataset([1, 2], [3, 4], [])
        progress = self.assertAdjudicatorProgress(0, 4, 3, 3, 0, 1.0)
        self.assertEqual(len(progress.unexpected_trackers()), 1)

    def test_adjudicator_progress_unexpected(self):
        self._create_adjudicator_progress_dataset([5], [1], [2])
        progress = self.assertAdjudicatorProgress(0, 3, 3, 0, 3, 0.0)
        self.assertEqual(len(progress.unexpected_trackers()), 3)
