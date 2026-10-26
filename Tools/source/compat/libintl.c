/* iNux compat: libintl without catalogs (see libintl.h). */
#include "libintl.h"

#include <stddef.h>

int _nl_msg_cat_cntr = 0;

static char s_domain[256] = "messages";

char *gettext(const char *msgid) { return (char *)msgid; }

char *dgettext(const char *domain, const char *msgid)
{
    (void)domain;
    return (char *)msgid;
}

char *dcgettext(const char *domain, const char *msgid, int category)
{
    (void)domain;
    (void)category;
    return (char *)msgid;
}

char *ngettext(const char *msgid, const char *msgid_plural, unsigned long n)
{
    return (char *)(n == 1 ? msgid : msgid_plural);
}

char *dngettext(const char *domain, const char *msgid, const char *msgid_plural, unsigned long n)
{
    (void)domain;
    return ngettext(msgid, msgid_plural, n);
}

char *dcngettext(const char *domain, const char *msgid, const char *msgid_plural, unsigned long n, int category)
{
    (void)domain;
    (void)category;
    return ngettext(msgid, msgid_plural, n);
}

char *textdomain(const char *domainname)
{
    if (domainname && *domainname) {
        size_t i = 0;
        for (; domainname[i] && i + 1 < sizeof(s_domain); ++i)
            s_domain[i] = domainname[i];
        s_domain[i] = 0;
    }
    return s_domain;
}

char *bindtextdomain(const char *domainname, const char *dirname)
{
    (void)domainname;
    return (char *)dirname;
}

char *bind_textdomain_codeset(const char *domainname, const char *codeset)
{
    (void)domainname;
    return (char *)codeset;
}
