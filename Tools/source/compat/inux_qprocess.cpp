// iNux compat: see inux_qprocess.h. Every launch fails with FailedToStart.
#include "inux_qprocess.h"

#if !QT_CONFIG(process)

#include <QtCore/qmetaobject.h>
#include <QtCore/qtimer.h>

QT_BEGIN_NAMESPACE

QProcess::QProcess(QObject *parent)
    : QIODevice(parent)
    , m_environment(QProcessEnvironment::systemEnvironment())
{
}

QProcess::~QProcess() = default;

void QProcess::failToStart()
{
    m_error = FailedToStart;
    m_state = NotRunning;
    setErrorString(QStringLiteral("Starting programs is not possible on iOS (%1)").arg(m_program));
    // The real QProcess reports this from the event loop.
    QTimer::singleShot(0, this, [this] {
        Q_EMIT errorOccurred(FailedToStart);
    });
}

void QProcess::start(const QString &program, const QStringList &arguments, OpenMode mode)
{
    m_program = program;
    m_arguments = arguments;
    start(mode);
}

void QProcess::start(OpenMode)
{
    failToStart();
}

void QProcess::startCommand(const QString &command, OpenMode mode)
{
    QStringList args = splitCommand(command);
    QString program = args.isEmpty() ? QString() : args.takeFirst();
    start(program, args, mode);
}

bool QProcess::startDetached(qint64 *pid)
{
    if (pid)
        *pid = 0;
    m_error = FailedToStart;
    return false;
}

bool QProcess::open(OpenMode mode)
{
    start(mode);
    return false;
}

QString QProcess::program() const { return m_program; }
void QProcess::setProgram(const QString &program) { m_program = program; }
QStringList QProcess::arguments() const { return m_arguments; }
void QProcess::setArguments(const QStringList &arguments) { m_arguments = arguments; }

QProcess::ProcessChannelMode QProcess::processChannelMode() const { return m_channelMode; }
void QProcess::setProcessChannelMode(ProcessChannelMode mode) { m_channelMode = mode; }
QProcess::InputChannelMode QProcess::inputChannelMode() const { return m_inputMode; }
void QProcess::setInputChannelMode(InputChannelMode mode) { m_inputMode = mode; }
QProcess::ProcessChannel QProcess::readChannel() const { return m_readChannel; }
void QProcess::setReadChannel(ProcessChannel channel) { m_readChannel = channel; }
void QProcess::closeReadChannel(ProcessChannel) {}
void QProcess::closeWriteChannel() {}

void QProcess::setStandardInputFile(const QString &) {}
void QProcess::setStandardOutputFile(const QString &, OpenMode) {}
void QProcess::setStandardErrorFile(const QString &, OpenMode) {}
void QProcess::setStandardOutputProcess(QProcess *) {}

std::function<void(void)> QProcess::childProcessModifier() const { return m_modifier; }
void QProcess::setChildProcessModifier(const std::function<void(void)> &modifier) { m_modifier = modifier; }

QString QProcess::workingDirectory() const { return m_workingDirectory; }
void QProcess::setWorkingDirectory(const QString &dir) { m_workingDirectory = dir; }

void QProcess::setEnvironment(const QStringList &environment)
{
    QProcessEnvironment env;
    for (const QString &entry : environment) {
        const qsizetype eq = entry.indexOf(QLatin1Char('='));
        if (eq > 0)
            env.insert(entry.left(eq), entry.mid(eq + 1));
    }
    m_environment = env;
}

QStringList QProcess::environment() const { return m_environment.toStringList(); }
void QProcess::setProcessEnvironment(const QProcessEnvironment &environment) { m_environment = environment; }
QProcessEnvironment QProcess::processEnvironment() const { return m_environment; }

QProcess::ProcessError QProcess::error() const { return m_error; }
QProcess::ProcessState QProcess::state() const { return m_state; }
qint64 QProcess::processId() const { return 0; }

bool QProcess::waitForStarted(int) { return false; }
bool QProcess::waitForReadyRead(int) { return false; }
bool QProcess::waitForBytesWritten(int) { return false; }
bool QProcess::waitForFinished(int) { return false; }

QByteArray QProcess::readAllStandardOutput() { return {}; }
QByteArray QProcess::readAllStandardError() { return {}; }

int QProcess::exitCode() const { return 0; }
QProcess::ExitStatus QProcess::exitStatus() const { return NormalExit; }

qint64 QProcess::bytesToWrite() const { return 0; }
bool QProcess::isSequential() const { return true; }
void QProcess::close() { QIODevice::close(); }

void QProcess::terminate() {}
void QProcess::kill() {}

void QProcess::setProcessState(ProcessState state)
{
    if (m_state == state)
        return;
    m_state = state;
    Q_EMIT stateChanged(state, QPrivateSignal());
}

qint64 QProcess::readData(char *, qint64) { return -1; }
qint64 QProcess::writeData(const char *, qint64) { return -1; }

int QProcess::execute(const QString &, const QStringList &)
{
    return -2; // Qt's documented value for "cannot be started"
}

bool QProcess::startDetached(const QString &, const QStringList &, const QString &, qint64 *pid)
{
    if (pid)
        *pid = 0;
    return false;
}

QStringList QProcess::systemEnvironment()
{
    return QProcessEnvironment::systemEnvironment().toStringList();
}

QString QProcess::nullDevice()
{
    return QStringLiteral("/dev/null");
}

#if QT_VERSION < QT_VERSION_CHECK(6, 10, 0)
// Older Qt has no QProcess at all without process support, so provide
// splitCommand with the same rules: whitespace separates arguments, double
// quotes group, and three consecutive quotes produce one literal quote.
QStringList QProcess::splitCommand(QStringView command)
{
    QStringList args;
    QString tmp;
    int quoteCount = 0;
    bool inQuote = false;
    for (qsizetype i = 0; i < command.size(); ++i) {
        if (command.at(i) == QLatin1Char('"')) {
            ++quoteCount;
            if (quoteCount == 3) {
                quoteCount = 0;
                tmp += command.at(i);
            }
            continue;
        }
        if (quoteCount) {
            if (quoteCount == 1)
                inQuote = !inQuote;
            quoteCount = 0;
        }
        if (!inQuote && command.at(i).isSpace()) {
            if (!tmp.isEmpty()) {
                args += tmp;
                tmp.clear();
            }
        } else {
            tmp += command.at(i);
        }
    }
    if (!tmp.isEmpty())
        args += tmp;
    return args;
}
#endif

QT_END_NAMESPACE

#endif // !QT_CONFIG(process)
