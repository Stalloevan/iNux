/* iNux compat: a minimal libintl for iOS builds.
 *
 * KDE's ki18n needs gettext's runtime library. iOS has none, and GNU
 * gettext does not cross-compile cleanly, so this provides the same
 * functions without translation catalogs: every call returns the original
 * (English) string, with the usual singular/plural choice. */
#ifndef INUX_LIBINTL_H
#define INUX_LIBINTL_H

#ifdef __cplusplus
extern "C" {
#endif

char *gettext(const char *msgid);
char *dgettext(const char *domain, const char *msgid);
char *dcgettext(const char *domain, const char *msgid, int category);
char *ngettext(const char *msgid, const char *msgid_plural, unsigned long n);
char *dngettext(const char *domain, const char *msgid, const char *msgid_plural, unsigned long n);
char *dcngettext(const char *domain, const char *msgid, const char *msgid_plural, unsigned long n, int category);
char *textdomain(const char *domainname);
char *bindtextdomain(const char *domainname, const char *dirname);
char *bind_textdomain_codeset(const char *domainname, const char *codeset);

extern int _nl_msg_cat_cntr;

#ifdef __cplusplus
}
#endif

#endif /* INUX_LIBINTL_H */
