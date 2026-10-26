// Minimal Qt Widgets app used to self-test the iNux source-build pipeline:
// if this compiles against Qt-for-iOS and packages into an IPA, the core
// path (Qt cross-build -> app bundle -> IPA) works.
#include <QApplication>
#include <QLabel>
#include <QPushButton>
#include <QVBoxLayout>
#include <QWidget>

int main(int argc, char **argv) {
    QApplication app(argc, argv);
    QWidget window;
    window.setWindowTitle("iNux Qt self-test");
    auto *layout = new QVBoxLayout(&window);
    auto *label = new QLabel("Built from source for iOS by iNux.");
    label->setAlignment(Qt::AlignCenter);
    auto *count = new QLabel("Taps: 0");
    count->setAlignment(Qt::AlignCenter);
    auto *button = new QPushButton("Tap me");
    layout->addWidget(label);
    layout->addWidget(count);
    layout->addWidget(button);
    int taps = 0;
    QObject::connect(button, &QPushButton::clicked, [&]() {
        count->setText(QString("Taps: %1").arg(++taps));
    });
    window.showMaximized();
    return app.exec();
}
