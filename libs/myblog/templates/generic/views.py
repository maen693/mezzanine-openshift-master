
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.messages import error
from django.core.urlresolvers import reverse
from django.db.models import get_model, ObjectDoesNotExist
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.simplejson import dumps
from django.utils.translation import ugettext_lazy as _

from mezzanine.conf import settings
from mezzanine.generic.forms import ThreadedCommentForm, RatingForm
from mezzanine.generic.models import Keyword
from mezzanine.utils.cache import add_cache_bypass
from mezzanine.utils.views import render, set_cookie, is_spam


@staff_member_required
def admin_keywords_submit(request):
    """
    Adds any new given keywords from the custom keywords field in the
    admin, and returns their IDs for use when saving a model with a
    keywords field.
    """
    ids, titles = [], []
    for title in request.POST.get("text_keywords", "").split(","):
        title = "".join([c for c in title if c.isalnum() or c in "- "])
        title = title.strip().lower()
        if title:
            keyword, created = Keyword.objects.get_or_create(title=title)
            id = str(keyword.id)
            if id not in ids:
                ids.append(id)
                titles.append(title)
    return HttpResponse("%s|%s" % (",".join(ids), ", ".join(titles)))


def initial_validation(request, prefix):
    """
    Returns the related model instance and post data to use in the
    comment/rating views below.

    Both comments and ratings have a ``prefix_ACCOUNT_REQUIRED``
    setting. If this is ``True`` and the user is unauthenticated, we
    store their post data in their session, and redirect to login with
    the view's url (also defined by the prefix arg) as the ``next``
    param. We can then check the session data once they log in,
    and complete the action authenticated.

    On successful post, we pass the related object and post data back,
    which may have come from the session, for each of the comments and
    ratings view functions to deal with as needed.
    """
    post_data = request.POST
    settings.use_editable()
    login_required_setting_name = prefix.upper() + "S_ACCOUNT_REQUIRED"
    posted_session_key = "unauthenticated_" + prefix
    if getattr(settings, login_required_setting_name, False):
        if not request.user.is_authenticated():
            request.session[posted_session_key] = request.POST
            error(request, _("You must logged in. Please log in or "
                             "sign up to complete this action."))
            url = "%s?next=%s" % (settings.LOGIN_URL, reverse(prefix))
            return redirect(url)
        elif posted_session_key in request.session:
            post_data = request.session.pop(posted_session_key)
    try:
        model = get_model(*post_data.get("content_type", "").split(".", 1))
        if model:
            obj = model.objects.get(id=post_data.get("object_pk", None))
    except (TypeError, ObjectDoesNotExist):
        return redirect("/")
    return obj, post_data


def comment(request, template="generic/comments.html"):
    """
    Handle a ``ThreadedCommentForm`` submission and redirect back to its
    related object.
    """
    response = initial_validation(request, "comment")
    if isinstance(response, HttpResponse):
        return response
    obj, post_data = response
    form = ThreadedCommentForm(request, obj, post_data)
    if form.is_valid():
        url = obj.get_absolute_url()
        if is_spam(request, form, url):
            return redirect(url)
        comment = form.save(request)
        response = redirect(add_cache_bypass(comment.get_absolute_url()))
        # Store commenter's details in a cookie for 90 days.
        for field in ThreadedCommentForm.cookie_fields:
            cookie_name = ThreadedCommentForm.cookie_prefix + field
            cookie_value = post_data.get(field, "")
            set_cookie(response, cookie_name, cookie_value)
        return response
    # Show errors with stand-alone comment form.
    context = {"obj": obj, "posted_comment_form": form}
    response = render(request, template, context)
    return response


def rating(request):
    """
    Handle a ``RatingForm`` submission and redirect back to its
    related object.
    """
    response = initial_validation(request, "rating")
    if isinstance(response, HttpResponse):
        return response
    obj, post_data = response
    url = obj.get_absolute_url()
    url = add_cache_bypass(url.split("#")[0]) + "#rating-%s" % obj.id
    response = redirect(url)
    rating_form = RatingForm(request, obj, post_data)
    if rating_form.is_valid():
        rating_form.save()
        if request.is_ajax():
            # Reload the object and return the new rating.
            obj = obj.__class__.objects.get(id=obj.id)
            fields = ("rating_avg", "rating_count", "rating_sum")
            json = dumps(dict([(f, getattr(obj, f)) for f in fields]))
            response = HttpResponse(json)
        ratings = ",".join(rating_form.previous + [rating_form.current])
        set_cookie(response, "mezzanine-rating", ratings)
    return response
