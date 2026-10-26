// iNux iOS bootstrap, linked into every ported app.
//
// KDE apps find their data (themes, levels, sounds, icons) through
// QStandardPaths, which on iOS only looks in the app's own
// Library/Application Support folder, not in the app bundle. The build puts
// the app's installed share/ data inside the bundle; before main() runs,
// this links each entry of <bundle>/share into Library/Application Support,
// so lookups such as "kbreakout/levelsets/..." resolve to the bundled files.
// Links are refreshed on every launch, because the bundle path changes when
// the app is updated.
#include <cerrno>
#include <climits>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <mach-o/dyld.h>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

namespace {

std::string dirName(const std::string &path)
{
    const auto slash = path.find_last_of('/');
    return slash == std::string::npos ? std::string(".") : path.substr(0, slash);
}

void makeDirs(const std::string &path)
{
    std::string partial;
    for (size_t i = 0; i < path.size(); ++i) {
        partial += path[i];
        if ((path[i] == '/' && i > 0) || i + 1 == path.size())
            mkdir(partial.c_str(), 0755);
    }
}

void linkShareIntoAppSupport()
{
    char exe[PATH_MAX];
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) != 0)
        return;
    char real[PATH_MAX];
    if (!realpath(exe, real))
        return;
    const std::string bundle = dirName(real);
    const std::string share = bundle + "/share";

    // QML modules installed outside Qt (e.g. org.kde.games.core) are bundled
    // in <bundle>/qml-modules; tell the QML engine before it starts.
    const std::string qml = bundle + "/qml-modules";
    struct stat qst;
    if (stat(qml.c_str(), &qst) == 0) {
        const char *old = getenv("QML_IMPORT_PATH");
        std::string value = qml;
        if (old && *old)
            value += std::string(":") + old;
        setenv("QML_IMPORT_PATH", value.c_str(), 1);
    }
    const char *home = getenv("HOME");
    if (!home)
        return;
    const std::string support = std::string(home) + "/Library/Application Support";
    makeDirs(support);

    DIR *dir = opendir(share.c_str());
    if (!dir)
        return;
    while (dirent *e = readdir(dir)) {
        if (e->d_name[0] == '.')
            continue;
        const std::string target = share + "/" + e->d_name;
        const std::string link = support + "/" + e->d_name;
        struct stat st;
        if (lstat(link.c_str(), &st) == 0) {
            if (!S_ISLNK(st.st_mode))
                continue; // a real folder the app wrote; leave it alone
            unlink(link.c_str());
        }
        symlink(target.c_str(), link.c_str());
    }
    closedir(dir);
}

struct Bootstrap {
    Bootstrap() { linkShareIntoAppSupport(); }
} s_bootstrap;

} // namespace
