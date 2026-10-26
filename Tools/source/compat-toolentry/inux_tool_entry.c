/* iNux: entry point for helper programs inside library builds.
 *
 * Qt for iOS makes every executable start at qt_main_wrapper, which comes
 * from Qt's iOS platform plugin and starts a UIKit app. Frameworks also
 * build command-line tools (code generators, inspectors) that are never run
 * on iOS - the macOS host builds are used instead - but they still have to
 * link. This archive is only on the link line of library builds, never the
 * app's, where Qt's real qt_main_wrapper is used. */
extern int main(int argc, char *argv[]);

int qt_main_wrapper(int argc, char *argv[])
{
    return main(argc, argv);
}
