from django.conf import settings

from . import permissions as P


def globals(request):
    u = getattr(request, "user", None)
    ctx = {"SCHOOL_NAME_AR": settings.SCHOOL_NAME_AR, "SCHOOL_NAME_EN": settings.SCHOOL_NAME_EN,
           "UNIVERSITY_AR": settings.UNIVERSITY_AR, "UNIVERSITY_EN": settings.UNIVERSITY_EN}
    if u is not None and u.is_authenticated:
        ctx.update(is_exec=P.is_exec(u), is_admin=P.is_admin(u), sees_everything=P.sees_everything(u),
                   unread_count=u.notifications.filter(is_read=False).count())
    return ctx
