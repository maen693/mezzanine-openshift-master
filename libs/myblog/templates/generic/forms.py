
from django import forms
from django.contrib.comments.forms import CommentSecurityForm, CommentForm
from django.contrib.comments.signals import comment_was_posted
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _

from mezzanine.conf import settings
from mezzanine.core.forms import Html5Mixin
from mezzanine.generic.models import Keyword, ThreadedComment, Rating
from mezzanine.utils.cache import add_cache_bypass
from mezzanine.utils.email import send_mail_template
from mezzanine.utils.views import ip_for_request


class KeywordsWidget(forms.MultiWidget):
    """
    Form field for the ``KeywordsField`` generic relation field. Since
    the admin with model forms has no form field for generic
    relations, this form field provides a single field for managing
    the keywords. It contains two actual widgets, a text input for
    entering keywords, and a hidden input that stores the ID of each
    ``Keyword`` instance.

    The attached JavaScript adds behaviour so that when the form is
    submitted, an AJAX post is made that passes the list of keywords
    in the text input, and returns a list of keyword IDs which are
    then entered into the hidden input before the form submits. The
    list of IDs in the hidden input is what is used when retrieving
    an actual value from the field for the form.
    """

    class Media:
        js = ("mezzanine/js/%s" % settings.JQUERY_FILENAME,
              "mezzanine/js/admin/keywords_field.js",)

    def __init__(self, attrs=None):
        """
        Setup the text and hidden form field widgets.
        """
        widgets = (forms.HiddenInput,
                   forms.TextInput(attrs={"class": "vTextField"}))
        super(KeywordsWidget, self).__init__(widgets, attrs)
        self._ids = []

    def decompress(self, value):
        """
        Takes the sequence of ``AssignedKeyword`` instances and splits
        them into lists of keyword IDs and titles each mapping to one
        of the form field widgets.
        """
        if hasattr(value, "select_related"):
            keywords = [a.keyword for a in value.select_related("keyword")]
            if keywords:
                keywords = [(str(k.id), k.title) for k in keywords]
                self._ids, words = zip(*keywords)
                return (",".join(self._ids), ", ".join(words))
        return ("", "")

    def format_output(self, rendered_widgets):
        """
        Wraps the output HTML with a list of all available ``Keyword``
        instances that can be clicked on to toggle a keyword.
        """
        rendered = super(KeywordsWidget, self).format_output(rendered_widgets)
        links = ""
        for keyword in Keyword.objects.all().order_by("title"):
            prefix = "+" if str(keyword.id) not in self._ids else "-"
            links += ("<a href='#'>%s%s</a>" % (prefix, unicode(keyword)))
        rendered += mark_safe("<p class='keywords-field'>%s</p>" % links)
        return rendered

    def value_from_datadict(self, data, files, name):
        """
        Return the comma separated list of keyword IDs for use in
        ``KeywordsField.save_form_data()``.
        """
        return data.get("%s_0" % name, "")


class ThreadedCommentForm(CommentForm, Html5Mixin):

    name = forms.CharField(label=_("Name"), help_text=_("required"),
                           max_length=50)
    email = forms.EmailField(label=_("Email"),
                             help_text=_("required (not published)"))
    url = forms.URLField(label=_("Website"), help_text=_("optional"),
                         required=False)

    # These are used to get/set prepopulated fields via cookies.
    cookie_fields = ("name", "email", "url")
    cookie_prefix = "mezzanine-comment-"

    def __init__(self, request, *args, **kwargs):
        """
        Set some initial field values from cookies or the logged in
        user, and apply some HTML5 attributes to the fields if the
        ``FORMS_USE_HTML5`` setting is ``True``.
        """
        kwargs.setdefault("initial", {})
        user = request.user
        for field in ThreadedCommentForm.cookie_fields:
            cookie_name = ThreadedCommentForm.cookie_prefix + field
            value = request.COOKIES.get(cookie_name, "")
            if not value and user.is_authenticated():
                if field == "name":
                    value = user.get_full_name()
                    if not value and user.username != user.email:
                        value = user.username
                elif field == "email":
                    value = user.email
            kwargs["initial"][field] = value
        super(ThreadedCommentForm, self).__init__(*args, **kwargs)

    def get_comment_model(self):
        """
        Use the custom comment model instead of the built-in one.
        """
        return ThreadedComment

    def save(self, request):
        """
        Saves a new comment and sends any notification emails.
        """
        comment = self.get_comment_object()
        obj = comment.content_object
        if request.user.is_authenticated():
            comment.user = request.user
        comment.by_author = request.user == getattr(obj, "user", None)
        comment.ip_address = ip_for_request(request)
        comment.replied_to_id = self.data.get("replied_to")
        comment.save()
        comment_was_posted.send(sender=comment.__class__, comment=comment,
                                request=request)
        notify_emails = settings.COMMENTS_NOTIFICATION_EMAILS.split(",")
        notify_emails = filter(None, map(str.strip, notify_emails))
        if notify_emails:
            subject = _("New comment for: ") + unicode(obj)
            context = {
                "comment": comment,
                "comment_url": add_cache_bypass(comment.get_absolute_url()),
                "request": request,
                "obj": obj,
            }
            send_mail_template(subject, "email/comment_notification",
                               settings.DEFAULT_FROM_EMAIL, notify_emails,
                               context, fail_silently=settings.DEBUG)
        return comment


class RatingForm(CommentSecurityForm):
    """
    Form for a rating. Subclasses ``CommentSecurityForm`` to make use
    of its easy setup for generic relations.
    """
    value = forms.ChoiceField(label="", widget=forms.RadioSelect,
                              choices=zip(*(settings.RATINGS_RANGE,) * 2))

    def __init__(self, request, *args, **kwargs):
        self.request = request
        super(RatingForm, self).__init__(*args, **kwargs)

    def clean(self):
        """
        Check unauthenticated user's cookie as a light check to
        prevent duplicate votes.
        """
        bits = (self.data["content_type"], self.data["object_pk"])
        self.current = "%s.%s" % bits
        request = self.request
        self.previous = request.COOKIES.get("mezzanine-rating", "").split(",")
        already_rated = self.current in self.previous
        if already_rated and not self.request.user.is_authenticated():
            raise forms.ValidationError(_("Already rated."))
        return self.cleaned_data

    def save(self):
        """
        Saves a new rating - authenticated users can update the
        value if they've previously rated.
        """
        user = self.request.user
        rating_value = self.cleaned_data["value"]
        rating_manager = self.target_object.get_ratingfield_manager()
        if user.is_authenticated():
            try:
                rating_instance = rating_manager.get(user=user)
            except Rating.DoesNotExist:
                rating_instance = Rating(user=user, value=rating_value)
                rating_manager.add(rating_instance)
            else:
                rating_instance.value = rating_value
                rating_instance.save()
        else:
            rating_instance = Rating(value=rating_value)
            rating_manager.add(rating_instance)
        return rating_instance
