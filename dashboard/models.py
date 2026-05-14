from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class BugSource(models.Model):
    SOURCE_TYPES = [
        ('launchpad', 'Launchpad Project'),
        ('launchpad_package', 'Launchpad Package'),
        ('github', 'GitHub Issues'),
        ('bugzilla', 'Bugzilla'),
    ]
    name = models.CharField(max_length=100)
    source_type = models.CharField(max_length=50, choices=SOURCE_TYPES)
    identifier = models.CharField(max_length=200)

    def __str__(self):
        return f"{self.get_source_type_display()}: {self.identifier}"


class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Bug(models.Model):
    external_id = models.CharField(max_length=200, unique=True)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=50)
    priority = models.CharField(max_length=50, blank=True)
    sources = models.ManyToManyField(BugSource, related_name='bugs')
    url = models.URLField(max_length=500, blank=True)
    last_updated = models.DateTimeField()
    tags = models.ManyToManyField(Tag, related_name='bugs', blank=True)

    class Meta:
        ordering = ['-last_updated']

    def __str__(self):
        return f"{self.external_id}: {self.title}"


class Preset(models.Model):
    name = models.CharField(max_length=100, unique=True)
    sources = models.ManyToManyField(BugSource, related_name='presets')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class GitHubPR(models.Model):
    pr_number = models.IntegerField()
    repo = models.CharField(max_length=200)
    title = models.CharField(max_length=500)
    url = models.URLField(max_length=500)
    state = models.CharField(max_length=50)
    bugs = models.ManyToManyField(Bug, related_name='github_prs')
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('repo', 'pr_number')]
        ordering = ['-pr_number']

    def __str__(self):
        return f"{self.repo}#{self.pr_number}: {self.title}"


class BugCorrelationQuerySet(models.QuerySet):
    def for_bug(self, bug):
        return self.filter(bugs=bug).distinct()


class BugCorrelation(models.Model):
    bugs = models.ManyToManyField(Bug, related_name="correlations")
    confidence_score = models.FloatField(
        default=0.0,
        db_index=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )
    match_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = BugCorrelationQuerySet.as_manager()

    class Meta:
        ordering = ["-confidence_score"]

    def __str__(self):
        bug_ids = ", ".join(self.bugs.values_list("external_id", flat=True)[:3])
        return f"Correlation ({self.confidence_score:.2f}): {bug_ids}"


class View(models.Model):
    preset = models.ForeignKey(Preset, on_delete=models.CASCADE, related_name="views")
    name = models.CharField(max_length=100)
    tag = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        unique_together = [("preset", "name")]

    def __str__(self):
        return f"{self.preset.name} / {self.name}"

