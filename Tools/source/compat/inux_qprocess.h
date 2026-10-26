// iNux compat: QProcess for Qt builds that have no process support (iOS).
//
// Qt for iOS is built with the "process" feature off, because iOS apps
// cannot create child processes. A lot of KDE code still mentions QProcess
// (KProcess, "open with" helpers, archive tools). This header supplies a
// QProcess with the Qt 6 public API so that code compiles unchanged; at
// runtime every attempt to run a program fails with FailedToStart, which
// is what the real class reports when a program cannot be launched, so
// callers take their existing error paths.
//
// It is force-included (-include) into C++ files of KDE/Qt modules built
// for iOS. On a Qt that has QProcess it does nothing.
#ifndef INUX_QPROCESS_H
#define INUX_QPROCESS_H

// Only active in C++ files that can see Qt: CMake's configure checks also
// get this flag, and must not be affected.
#if defined(__cplusplus) && defined(__has_include)
#if __has_include(<QtCore/qglobal.h>)

#include <QtCore/qglobal.h>
#include <QtCore/qtcore-config.h>

#if !QT_CONFIG(process)

#include <QtCore/qiodevice.h>
#include <QtCore/qstringlist.h>
#include <QtCore/qprocess.h> // QProcessEnvironment (feature processenvironment)
#include <functional>

QT_BEGIN_NAMESPACE

class QProcess : public QIODevice
{
    Q_OBJECT
public:
    enum ProcessError { FailedToStart, Crashed, Timedout, ReadError, WriteError, UnknownError };
    Q_ENUM(ProcessError)
    enum ProcessState { NotRunning, Starting, Running };
    Q_ENUM(ProcessState)
    enum ProcessChannel { StandardOutput, StandardError };
    Q_ENUM(ProcessChannel)
    enum ProcessChannelMode {
        SeparateChannels,
        MergedChannels,
        ForwardedChannels,
        ForwardedOutputChannel,
        ForwardedErrorChannel
    };
    Q_ENUM(ProcessChannelMode)
    enum InputChannelMode { ManagedInputChannel, ForwardedInputChannel };
    Q_ENUM(InputChannelMode)
    enum ExitStatus { NormalExit, CrashExit };
    Q_ENUM(ExitStatus)

    explicit QProcess(QObject *parent = nullptr);
    ~QProcess() override;

    void start(const QString &program, const QStringList &arguments = {}, OpenMode mode = ReadWrite);
    void start(OpenMode mode = ReadWrite);
    void startCommand(const QString &command, OpenMode mode = ReadWrite);
    bool startDetached(qint64 *pid = nullptr);
    bool open(OpenMode mode = ReadWrite) override;

    QString program() const;
    void setProgram(const QString &program);
    QStringList arguments() const;
    void setArguments(const QStringList &arguments);

    ProcessChannelMode processChannelMode() const;
    void setProcessChannelMode(ProcessChannelMode mode);
    InputChannelMode inputChannelMode() const;
    void setInputChannelMode(InputChannelMode mode);
    ProcessChannel readChannel() const;
    void setReadChannel(ProcessChannel channel);
    void closeReadChannel(ProcessChannel channel);
    void closeWriteChannel();

    void setStandardInputFile(const QString &fileName);
    void setStandardOutputFile(const QString &fileName, OpenMode mode = Truncate);
    void setStandardErrorFile(const QString &fileName, OpenMode mode = Truncate);
    void setStandardOutputProcess(QProcess *destination);

    std::function<void(void)> childProcessModifier() const;
    void setChildProcessModifier(const std::function<void(void)> &modifier);

    QString workingDirectory() const;
    void setWorkingDirectory(const QString &dir);

    void setEnvironment(const QStringList &environment);
    QStringList environment() const;
    void setProcessEnvironment(const QProcessEnvironment &environment);
    QProcessEnvironment processEnvironment() const;

    ProcessError error() const;
    ProcessState state() const;
    qint64 processId() const;

    bool waitForStarted(int msecs = 30000);
    bool waitForReadyRead(int msecs = 30000) override;
    bool waitForBytesWritten(int msecs = 30000) override;
    bool waitForFinished(int msecs = 30000);

    QByteArray readAllStandardOutput();
    QByteArray readAllStandardError();

    int exitCode() const;
    ExitStatus exitStatus() const;

    qint64 bytesToWrite() const override;
    bool isSequential() const override;
    void close() override;

    static int execute(const QString &program, const QStringList &arguments = {});
    static bool startDetached(const QString &program, const QStringList &arguments = {},
                              const QString &workingDirectory = QString(), qint64 *pid = nullptr);
    static QStringList systemEnvironment();
    static QString nullDevice();
    static QStringList splitCommand(QStringView command);

public Q_SLOTS:
    void terminate();
    void kill();

Q_SIGNALS:
    void started(QPrivateSignal);
    void finished(int exitCode, QProcess::ExitStatus exitStatus = NormalExit);
    void errorOccurred(QProcess::ProcessError error);
    void stateChanged(QProcess::ProcessState state, QPrivateSignal);
    void readyReadStandardOutput(QPrivateSignal);
    void readyReadStandardError(QPrivateSignal);

protected:
    void setProcessState(ProcessState state);
    qint64 readData(char *data, qint64 maxlen) override;
    qint64 writeData(const char *data, qint64 len) override;

private:
    void failToStart();

    QString m_program;
    QStringList m_arguments;
    QString m_workingDirectory;
    QProcessEnvironment m_environment;
    std::function<void(void)> m_modifier;
    ProcessChannelMode m_channelMode = SeparateChannels;
    InputChannelMode m_inputMode = ManagedInputChannel;
    ProcessChannel m_readChannel = StandardOutput;
    ProcessError m_error = UnknownError;
    ProcessState m_state = NotRunning;
};

QT_END_NAMESPACE

#endif // !QT_CONFIG(process)
#endif // __has_include(<QtCore/qglobal.h>)
#endif // __cplusplus && __has_include
#endif // INUX_QPROCESS_H
