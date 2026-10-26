/* iNux GTK self-test: a GTK 4 app compiled from source for iOS. */
#include <gtk/gtk.h>

static int clicks = 0;

static void
on_click (GtkButton *button, GtkLabel *label)
{
  char *text = g_strdup_printf ("Tapped %d time%s", ++clicks, clicks == 1 ? "" : "s");
  gtk_label_set_text (label, text);
  g_free (text);
}

static void
on_entry_changed (GtkEditable *editable, GtkLabel *echo)
{
  const char *text = gtk_editable_get_text (editable);
  gtk_label_set_text (echo, *text ? text : "Type something above");
}

static void
on_about (GSimpleAction *action, GVariant *param, gpointer window)
{
  gtk_show_about_dialog (GTK_WINDOW (window),
                         "program-name", "iNux GTK",
                         "comments", "GTK 4 with a UIKit backend",
                         "version", "1.0",
                         NULL);
}

static void
on_dialog (GtkButton *button, GtkWindow *parent)
{
  GtkWidget *win = gtk_window_new ();
  GtkWidget *box = gtk_box_new (GTK_ORIENTATION_VERTICAL, 12);
  GtkWidget *close = gtk_button_new_with_label ("Close");

  gtk_window_set_transient_for (GTK_WINDOW (win), parent);
  gtk_window_set_modal (GTK_WINDOW (win), TRUE);
  gtk_window_set_title (GTK_WINDOW (win), "Secondary window");
  gtk_widget_set_margin_top (box, 24);
  gtk_widget_set_margin_bottom (box, 24);
  gtk_widget_set_margin_start (box, 24);
  gtk_widget_set_margin_end (box, 24);
  gtk_box_append (GTK_BOX (box), gtk_label_new ("A transient window, centred over its parent."));
  gtk_box_append (GTK_BOX (box), close);
  g_signal_connect_swapped (close, "clicked", G_CALLBACK (gtk_window_destroy), win);
  gtk_window_set_child (GTK_WINDOW (win), box);
  gtk_window_present (GTK_WINDOW (win));
}

static void
activate (GtkApplication *app)
{
  GtkWidget *window = gtk_application_window_new (app);
  GtkWidget *header = gtk_header_bar_new ();
  GtkWidget *menu_button = gtk_menu_button_new ();
  GMenu *menu = g_menu_new ();
  GtkWidget *box = gtk_box_new (GTK_ORIENTATION_VERTICAL, 12);
  GtkWidget *label = gtk_label_new ("Hello from GTK on iOS");
  GtkWidget *button = gtk_button_new_with_label ("Tap me");
  GtkWidget *entry = gtk_entry_new ();
  GtkWidget *echo = gtk_label_new ("Type something above");
  GtkWidget *dialog_button = gtk_button_new_with_label ("Open a window");
  const char *choices[] = { "Apples", "Oranges", "Pears", NULL };
  GtkWidget *dropdown = gtk_drop_down_new_from_strings (choices);
  GtkWidget *switch_row = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 12);
  GtkWidget *sw = gtk_switch_new ();
  GtkWidget *list = gtk_list_box_new ();
  GtkWidget *scroller = gtk_scrolled_window_new ();
  GSimpleAction *about = g_simple_action_new ("about", NULL);

  gtk_window_set_title (GTK_WINDOW (window), "iNux GTK");
  gtk_window_set_default_size (GTK_WINDOW (window), 400, 700);

  g_menu_append (menu, "About", "win.about");
  gtk_menu_button_set_menu_model (GTK_MENU_BUTTON (menu_button), G_MENU_MODEL (menu));
  gtk_menu_button_set_icon_name (GTK_MENU_BUTTON (menu_button), "open-menu-symbolic");
  gtk_header_bar_pack_end (GTK_HEADER_BAR (header), menu_button);
  gtk_window_set_titlebar (GTK_WINDOW (window), header);
  g_signal_connect (about, "activate", G_CALLBACK (on_about), window);
  g_action_map_add_action (G_ACTION_MAP (window), G_ACTION (about));

  gtk_widget_set_margin_top (box, 18);
  gtk_widget_set_margin_bottom (box, 18);
  gtk_widget_set_margin_start (box, 18);
  gtk_widget_set_margin_end (box, 18);
  gtk_widget_add_css_class (label, "title-2");

  gtk_entry_set_placeholder_text (GTK_ENTRY (entry), "Type here");
  g_signal_connect (button, "clicked", G_CALLBACK (on_click), label);
  g_signal_connect (entry, "changed", G_CALLBACK (on_entry_changed), echo);
  g_signal_connect (dialog_button, "clicked", G_CALLBACK (on_dialog), window);

  gtk_box_append (GTK_BOX (switch_row), gtk_label_new ("A switch"));
  gtk_widget_set_hexpand (gtk_box_get_first_child (GTK_BOX (switch_row)), TRUE);
  gtk_widget_set_halign (gtk_box_get_first_child (GTK_BOX (switch_row)), GTK_ALIGN_START);
  gtk_box_append (GTK_BOX (switch_row), sw);

  for (int i = 1; i <= 60; i++)
    {
      char *text = g_strdup_printf ("Row %d", i);
      GtkWidget *row = gtk_label_new (text);
      gtk_widget_set_margin_top (row, 10);
      gtk_widget_set_margin_bottom (row, 10);
      gtk_list_box_append (GTK_LIST_BOX (list), row);
      g_free (text);
    }
  gtk_scrolled_window_set_child (GTK_SCROLLED_WINDOW (scroller), list);
  gtk_widget_set_vexpand (scroller, TRUE);
  gtk_widget_add_css_class (list, "boxed-list");

  gtk_box_append (GTK_BOX (box), label);
  gtk_box_append (GTK_BOX (box), button);
  gtk_box_append (GTK_BOX (box), entry);
  gtk_box_append (GTK_BOX (box), echo);
  gtk_box_append (GTK_BOX (box), dropdown);
  gtk_box_append (GTK_BOX (box), switch_row);
  gtk_box_append (GTK_BOX (box), dialog_button);
  gtk_box_append (GTK_BOX (box), scroller);

  gtk_window_set_child (GTK_WINDOW (window), box);
  gtk_window_present (GTK_WINDOW (window));
}

int
main (int argc, char **argv)
{
  GtkApplication *app = gtk_application_new ("org.inux.GtkSelfTest", G_APPLICATION_DEFAULT_FLAGS);
  int status;

  g_signal_connect (app, "activate", G_CALLBACK (activate), NULL);
  status = g_application_run (G_APPLICATION (app), argc, argv);
  g_object_unref (app);
  return status;
}
