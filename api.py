// agenda.c — GTK3 + C, sem BD (TXT)
// ✅ Agenda + Clientes + Fotos antes/depois + Logo
// ✅ Conflitos por barbeiro + duplo clique abre cliente
// ✅ Botão 📧 Email (mailto) + filtros barbeiro/estado
// ✅ Exportar dia para PDF (cairo-pdf)
// ✅ Faturação (OPÇÃO 1): global por DIA vs MÊS, com "Pago?" + pago parcial + saldo do cliente
//
// Compile (Linux):
//   sudo apt-get install -y libgtk-3-dev libcairo2-dev
//   gcc -std=c99 -Wall -Wextra agenda.c `pkg-config --cflags --libs gtk+-3.0` -o agenda
//
// Compile (MSYS2 / Windows - MINGW64):
//   pacman -S --needed mingw-w64-x86_64-gtk3 mingw-w64-x86_64-pkgconf mingw-w64-x86_64-cairo
//   gcc -std=c99 -Wall -Wextra agenda.c -o agenda.exe `pkg-config --cflags --libs gtk+-3.0`
//
// Data dir:
//   barbearia_data/logo.png  (ou .jpg/.jpeg)
//   barbearia_data/clientes/<ID_NOME>/cliente.txt + fotos
//   barbearia_data/agenda/YYYY-MM-DD.txt
//   barbearia_data/faturacao.txt

from flask import Flask, request, jsonify
from flask_cors import CORS
from collections import deque
import os, time, secrets, json

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ... resto igual ao teu código ...

#include <gtk/gtk.h>
#include <glib.h>
#include <glib/gstdio.h>
#include <string.h>
#include <errno.h>
#include <cairo.h>
#include <cairo-pdf.h>
#include <stdarg.h>

/* ----------------- paths ----------------- */
#define APP_DATA_DIR "barbearia_data"
#define CLIENTS_DIR  APP_DATA_DIR G_DIR_SEPARATOR_S "clientes"
#define AGENDA_DIR   APP_DATA_DIR G_DIR_SEPARATOR_S "agenda"
#define NEXT_CLIENT_ID_FILE APP_DATA_DIR G_DIR_SEPARATOR_S "next_client_id.txt"
#define BILLING_FILE APP_DATA_DIR G_DIR_SEPARATOR_S "faturacao.txt"

/* ----------------- models ----------------- */
typedef struct {
  gchar *id;          // interno
  gchar *name;
  gchar *phone;
  gchar *email;
  gchar *profession;
  gchar *age;
  gchar *notes;
  gchar *dir;
  gchar *before_path;
  gchar *after_path;
} Client;

typedef struct {
  GtkWindow *win;

  GtkNotebook *nb;
  gint page_agenda;
  gint page_clients;
  gint page_billing;

  GtkImage *img_logo;
  GtkLabel *lbl_title;

  // clients data
  GPtrArray *clients;        // Client*
  Client *current_client;    // pointer in clients array

  // clients UI
  GtkEntry *c_search;
  GtkTreeView *c_list;
  GtkListStore *c_store;

  GtkEntry *c_name;
  GtkEntry *c_phone;
  GtkEntry *c_email;
  GtkEntry *c_profession;
  GtkEntry *c_age;
  GtkTextView *c_notes;

  GtkImage *c_img_before;
  GtkImage *c_img_after;

  gchar *picked_before_src;
  gchar *picked_after_src;

  // agenda UI
  GtkCalendar *a_cal;
  GtkListStore *a_store;
  GtkTreeModelFilter *a_filter;
  GtkTreeView *a_list;

  GtkEntry *a_time;
  GtkSpinButton *a_dur;
  GtkComboBoxText *a_client;  // id interno
  GtkComboBoxText *a_service;
  GtkComboBoxText *a_barber;
  GtkComboBoxText *a_status;
  GtkTextView *a_notes;

  GtkComboBoxText *a_f_barber;
  GtkComboBoxText *a_f_status;

  gchar *agenda_file_loaded;
  gchar *filter_barber;
  gchar *filter_status;

  // billing (OPÇÃO 1)
  GtkCalendar *b_cal;            // escolher data (rápido)
  GtkToggleButton *b_mode_day;   // DIA
  GtkToggleButton *b_mode_month; // MÊS

  GtkTreeView *b_list;
  GtkListStore *b_store;
  GtkTreeModelFilter *b_filter;

  GtkLabel *b_lbl_total;
  GtkLabel *b_lbl_received;
  GtkLabel *b_lbl_due;

  GtkCheckButton *b_chk_paid;
  GtkEntry *b_paid_amount;
  GtkButton *b_btn_apply_paid;
  GtkButton *b_btn_set_paid;
  GtkButton *b_btn_set_unpaid;

  GtkLabel *b_lbl_client_balance;

} App;

/* ----------------- prototypes ----------------- */
static void ensure_dirs_or_die(GtkWindow *parent);
static void msg(GtkWindow *parent, GtkMessageType type, const char *title, const char *fmt, ...);
static gboolean confirm_yes_no(GtkWindow *parent, const char *title, const char *fmt, ...);

static void client_free(Client *c);
static Client* client_find_by_id(App *app, const gchar *id);

static gchar* sanitize_for_folder(const gchar *in);
static gchar* client_dir_for_fields(const gchar *id, const gchar *name);

static void set_image_from_path(GtkImage *img, const char *path);
static void open_image_viewer(GtkWindow *parent, const gchar *path, const gchar *title);

static gchar* get_textview_text(GtkTextView *tv);
static void set_textview_text(GtkTextView *tv, const gchar *txt);

static gchar* next_client_id(GtkWindow *parent);

static gchar* calendar_to_date_str(GtkCalendar *cal);
static gchar* agenda_file_for_calendar(GtkCalendar *cal);

static void load_all_clients(App *app);
static void agenda_clients_combo_refresh(App *app);
static void billing_clients_combo_refresh(App *app); // (não mostra ID; aqui é só para consistência)

static gboolean save_client_to_disk(App *app,
                                    const gchar *id, const gchar *name,
                                    const gchar *phone,
                                    const gchar *email,
                                    const gchar *profession,
                                    const gchar *age,
                                    const gchar *notes,
                                    const gchar *picked_before,
                                    const gchar *picked_after);

static void export_agenda_day_to_pdf(App *app);

static GtkWidget* labeled(GtkWidget *w, const char *label);
static void apply_compact_css(void);

/* ----------------- misc helpers ----------------- */
static inline const gchar* nz(const gchar *s) { return s ? s : ""; }

static void apply_compact_css(void)
{
  GtkCssProvider *css = gtk_css_provider_new();
  const char *data =
    "*{font-size:10pt;}"
    "window,dialog,.background{background-color:#1c1f24;color:#e4e7eb;}"
    "frame{background-color:#1f2329;border:1px solid #2d323a;border-radius:14px;}"
    "frame>label{color:#cfd6dd;font-weight:600;}"
    "notebook{background-color:#1c1f24;}"
    "notebook tab{background-color:#252a31;border:1px solid #323842;padding:8px 14px;border-top-left-radius:12px;border-top-right-radius:12px;}"
    "notebook tab:checked{background-color:#2e3440;}"
    "notebook>stack{background-color:#1c1f24;border:1px solid #323842;border-radius:14px;}"
    "entry,textview,combobox,spinbutton{background-color:#252a31;color:#e4e7eb;border:1px solid #363c47;border-radius:10px;padding:6px;min-height:26px;}"
    "entry:focus,textview:focus,combobox:focus,spinbutton:focus{border-color:#4c8bf5;}"
    "textview text{background-color:#252a31;color:#e4e7eb;}"
    "button{background-color:#2c313a;color:#e4e7eb;border:1px solid #3b424d;border-radius:12px;padding:6px 12px;}"
    "button:hover{background-color:#353b45;}"
    "button:active{background-color:#242932;}"
    "button.suggested-action{background-color:#4c8bf5;color:#ffffff;border:1px solid #4c8bf5;}"
    "button.suggested-action:hover{background-color:#5a98ff;}"
    "treeview.view{background-color:#1c1f24;color:#e4e7eb;font-size:9pt;}"
    "treeview.view:selected{background-color:#3a5ea8;color:#ffffff;}"
    "treeview header button{background-color:#252a31;color:#cfd6dd;border:1px solid #363c47;}"
    "scrollbar slider{background-color:#3b424d;border-radius:10px;}"
    "scrollbar trough{background-color:#20242a;}";
  gtk_css_provider_load_from_data(css, data, -1, NULL);
  GdkScreen *screen = gdk_screen_get_default();
  if (screen) gtk_style_context_add_provider_for_screen(screen, GTK_STYLE_PROVIDER(css),
                                                       GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
  g_object_unref(css);
}

static gboolean copy_file_overwrite(const char *src, const char *dst, GError **err) {
  GFile *fsrc = g_file_new_for_path(src);
  GFile *fdst = g_file_new_for_path(dst);
  gboolean ok = g_file_copy(fsrc, fdst, G_FILE_COPY_OVERWRITE, NULL, NULL, NULL, err);
  g_object_unref(fsrc);
  g_object_unref(fdst);
  return ok;
}

static void ensure_dirs_or_die(GtkWindow *parent) {
  if (g_mkdir_with_parents(APP_DATA_DIR, 0755) != 0 && errno != EEXIST) {
    GtkWidget *d = gtk_message_dialog_new(parent, GTK_DIALOG_MODAL, GTK_MESSAGE_ERROR, GTK_BUTTONS_OK,
                                         "Falha a criar %s: %s", APP_DATA_DIR, g_strerror(errno));
    gtk_dialog_run(GTK_DIALOG(d)); gtk_widget_destroy(d); exit(1);
  }
  if (g_mkdir_with_parents(CLIENTS_DIR, 0755) != 0 && errno != EEXIST) {
    GtkWidget *d = gtk_message_dialog_new(parent, GTK_DIALOG_MODAL, GTK_MESSAGE_ERROR, GTK_BUTTONS_OK,
                                         "Falha a criar %s: %s", CLIENTS_DIR, g_strerror(errno));
    gtk_dialog_run(GTK_DIALOG(d)); gtk_widget_destroy(d); exit(1);
  }
  if (g_mkdir_with_parents(AGENDA_DIR, 0755) != 0 && errno != EEXIST) {
    GtkWidget *d = gtk_message_dialog_new(parent, GTK_DIALOG_MODAL, GTK_MESSAGE_ERROR, GTK_BUTTONS_OK,
                                         "Falha a criar %s: %s", AGENDA_DIR, g_strerror(errno));
    gtk_dialog_run(GTK_DIALOG(d)); gtk_widget_destroy(d); exit(1);
  }
  // faturacao.txt é ficheiro, não diretório (cria vazio se não existir)
  if (!g_file_test(BILLING_FILE, G_FILE_TEST_EXISTS)) {
    g_file_set_contents(BILLING_FILE, "", -1, NULL);
  }
}

static void msg(GtkWindow *parent, GtkMessageType type, const char *title, const char *fmt, ...) {
  va_list ap; va_start(ap, fmt);
  gchar *body = g_strdup_vprintf(fmt, ap);
  va_end(ap);
  GtkWidget *d = gtk_message_dialog_new(parent, GTK_DIALOG_MODAL, type, GTK_BUTTONS_OK, "%s", body);
  gtk_window_set_title(GTK_WINDOW(d), title);
  gtk_dialog_run(GTK_DIALOG(d)); gtk_widget_destroy(d);
  g_free(body);
}

static gboolean confirm_yes_no(GtkWindow *parent, const char *title, const char *fmt, ...) {
  va_list ap; va_start(ap, fmt);
  gchar *body = g_strdup_vprintf(fmt, ap);
  va_end(ap);
  GtkWidget *d = gtk_message_dialog_new(parent, GTK_DIALOG_MODAL, GTK_MESSAGE_QUESTION, GTK_BUTTONS_YES_NO, "%s", body);
  gtk_window_set_title(GTK_WINDOW(d), title);
  gint r = gtk_dialog_run(GTK_DIALOG(d));
  gtk_widget_destroy(d); g_free(body);
  return (r == GTK_RESPONSE_YES);
}

/* ----------------- Client helpers ----------------- */
static void client_free(Client *c) {
  if (!c) return;
  g_free(c->id); g_free(c->name); g_free(c->phone); g_free(c->email);
  g_free(c->profession); g_free(c->age); g_free(c->notes);
  g_free(c->dir); g_free(c->before_path); g_free(c->after_path);
  g_free(c);
}

static Client* client_find_by_id(App *app, const gchar *id) {
  if (!app || !app->clients || !id) return NULL;
  for (guint i=0; i<app->clients->len; i++) {
    Client *c = g_ptr_array_index(app->clients, i);
    if (c && c->id && g_strcmp0(c->id, id) == 0) return c;
  }
  return NULL;
}

static gchar* sanitize_for_folder(const gchar *in) {
  if (!in || !*in) return g_strdup("Cliente");
  GString *out = g_string_new("");
  for (const char *p=in; *p; p = g_utf8_next_char(p)) {
    gunichar c = g_utf8_get_char(p);
    if (c==' ') { g_string_append_c(out,'_'); continue; }
    if (g_unichar_isalnum(c) || c=='_' || c=='-' || c=='.') {
      gchar buf[8]={0}; g_unichar_to_utf8(c, buf); g_string_append(out, buf);
    }
  }
  if (out->len==0) g_string_assign(out,"Cliente");
  return g_string_free(out,FALSE);
}

static gchar* client_dir_for_fields(const gchar *id, const gchar *name) {
  gchar *safe = sanitize_for_folder(name);
  gchar *folder = g_strdup_printf("%s_%s", (id&&*id)?id:"000000", safe);
  gchar *dir = g_build_filename(CLIENTS_DIR, folder, NULL);
  g_free(folder); g_free(safe);
  return dir;
}

/* ----------------- Image helpers ----------------- */
static void set_image_from_path(GtkImage *img, const char *path) {
  if (path && g_file_test(path, G_FILE_TEST_EXISTS)) {
    GError *err=NULL;
    GdkPixbuf *pb = gdk_pixbuf_new_from_file_at_scale(path, 420, 420, TRUE, &err);
    if (pb) { gtk_image_set_from_pixbuf(img, pb); g_object_unref(pb); return; }
    if (err) g_error_free(err);
  }
  gtk_image_set_from_icon_name(img, "image-missing", GTK_ICON_SIZE_DIALOG);
}

static void open_image_viewer(GtkWindow *parent, const gchar *path, const gchar *title) {
  if (!path || !g_file_test(path, G_FILE_TEST_EXISTS)) {
    msg(parent, GTK_MESSAGE_INFO, "Foto", "Ainda não há foto para mostrar.");
    return;
  }
  GtkWidget *dlg = gtk_dialog_new_with_buttons(title?title:"Foto", parent, GTK_DIALOG_MODAL,
                                               "_Fechar", GTK_RESPONSE_CLOSE, NULL);
  gtk_window_set_default_size(GTK_WINDOW(dlg), 900, 650);
  GtkWidget *content = gtk_dialog_get_content_area(GTK_DIALOG(dlg));
  GtkWidget *sc = gtk_scrolled_window_new(NULL,NULL);
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(sc), GTK_POLICY_AUTOMATIC, GTK_POLICY_AUTOMATIC);
  GtkWidget *img = gtk_image_new_from_file(path);
  gtk_container_add(GTK_CONTAINER(sc), img);
  gtk_container_add(GTK_CONTAINER(content), sc);
  gtk_widget_show_all(dlg);
  gtk_dialog_run(GTK_DIALOG(dlg));
  gtk_widget_destroy(dlg);
}

/* ----------------- Text helpers ----------------- */
static gchar* get_textview_text(GtkTextView *tv) {
  GtkTextBuffer *buf = gtk_text_view_get_buffer(tv);
  GtkTextIter a,b;
  gtk_text_buffer_get_start_iter(buf,&a);
  gtk_text_buffer_get_end_iter(buf,&b);
  return gtk_text_buffer_get_text(buf,&a,&b,FALSE);
}
static void set_textview_text(GtkTextView *tv, const gchar *txt) {
  GtkTextBuffer *buf = gtk_text_view_get_buffer(tv);
  gtk_text_buffer_set_text(buf, txt?txt:"", -1);
}

/* ----------------- IDs & dates ----------------- */
static gchar* next_client_id(GtkWindow *parent) {
  gchar *contents=NULL; gsize len=0;
  gint id=1;
  if (g_file_get_contents(NEXT_CLIENT_ID_FILE, &contents, &len, NULL) && contents) {
    id = (gint)g_ascii_strtoll(contents, NULL, 10);
    if (id < 1) id = 1;
  }
  g_free(contents);
  gchar *id_str = g_strdup_printf("%06d", id);
  gchar *new_contents = g_strdup_printf("%d\n", id+1);
  if (!g_file_set_contents(NEXT_CLIENT_ID_FILE, new_contents, -1, NULL)) {
    msg(parent, GTK_MESSAGE_WARNING, "Aviso",
        "Não consegui atualizar %s. Vou continuar na mesma.", NEXT_CLIENT_ID_FILE);
  }
  g_free(new_contents);
  return id_str;
}

static gchar* calendar_to_date_str(GtkCalendar *cal) {
  guint y,m,d; gtk_calendar_get_date(cal,&y,&m,&d);
  return g_strdup_printf("%04u-%02u-%02u", y, m+1, d);
}
static gchar* agenda_file_for_calendar(GtkCalendar *cal) {
  gchar *ds = calendar_to_date_str(cal);
  gchar *fp = g_build_filename(AGENDA_DIR, ds, NULL);
  gchar *full = g_strconcat(fp, ".txt", NULL);
  g_free(ds); g_free(fp);
  return full;
}

/* ----------------- KV parse ----------------- */
static gchar* kv_get(GHashTable *h, const char *k) {
  gpointer v = g_hash_table_lookup(h, k);
  return v ? g_strdup((const char*)v) : NULL;
}
static GHashTable* parse_kv_file(const gchar *path) {
  gchar *contents=NULL; gsize len=0;
  if (!g_file_get_contents(path, &contents, &len, NULL) || !contents) return NULL;
  GHashTable *h = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, g_free);
  gchar **lines = g_strsplit(contents, "\n", -1);
  for (int i=0; lines && lines[i]; i++) {
    if (!lines[i][0]) continue;
    gchar **kv = g_strsplit(lines[i], "=", 2);
    if (kv[0] && kv[1]) g_hash_table_insert(h, g_strdup(kv[0]), g_strdup(kv[1]));
    g_strfreev(kv);
  }
  g_strfreev(lines); g_free(contents);
  return h;
}
static Client* load_client_from_dir(const gchar *dir) {
  gchar *txt = g_build_filename(dir, "cliente.txt", NULL);
  if (!g_file_test(txt, G_FILE_TEST_EXISTS)) { g_free(txt); return NULL; }
  GHashTable *h = parse_kv_file(txt);
  g_free(txt);
  if (!h) return NULL;

  Client *c = g_new0(Client,1);
  c->id = kv_get(h,"id");
  c->name = kv_get(h,"nome");
  c->phone = kv_get(h,"telefone");
  c->email = kv_get(h,"email");
  c->profession = kv_get(h,"profissao");
  c->age = kv_get(h,"idade");

  gchar *notes_esc = kv_get(h,"notas");
  if (notes_esc) {
    gchar *un = g_strcompress(notes_esc);
    c->notes = un ? un : g_strdup("");
    g_free(notes_esc);
  } else c->notes = g_strdup("");

  c->dir = g_strdup(dir);

  gchar *before_rel = kv_get(h,"foto_antes");
  gchar *after_rel  = kv_get(h,"foto_depois");
  if (before_rel && *before_rel) c->before_path = g_build_filename(dir, before_rel, NULL);
  if (after_rel && *after_rel) c->after_path = g_build_filename(dir, after_rel, NULL);
  g_free(before_rel); g_free(after_rel);

  g_hash_table_destroy(h);
  return c;
}

/* ----------------- Clients store ----------------- */
enum { C_COL_ID=0, C_COL_NAME, C_COL_PHONE, C_COL_EMAIL, C_NCOLS };

static void clients_store_clear(App *app) { if (app && app->c_store) gtk_list_store_clear(app->c_store); }
static void clients_store_add(App *app, Client *c) {
  if (!app || !app->c_store || !c) return;
  GtkTreeIter it; gtk_list_store_append(app->c_store,&it);
  gtk_list_store_set(app->c_store,&it,
                     C_COL_ID, nz(c->id),
                     C_COL_NAME, nz(c->name),
                     C_COL_PHONE, nz(c->phone),
                     C_COL_EMAIL, nz(c->email),
                     -1);
}
static void clients_store_refresh(App *app, const gchar *filter) {
  if (!app || !app->c_store) return;
  clients_store_clear(app);
  if (!app->clients) return;

  for (guint i=0;i<app->clients->len;i++){
    Client *c = g_ptr_array_index(app->clients,i);
    if (!c) continue;

    if (filter && *filter) {
      gchar *f = g_utf8_strdown(filter,-1);
      gchar *name = g_utf8_strdown(nz(c->name),-1);
      gchar *phone= g_utf8_strdown(nz(c->phone),-1);
      gchar *email= g_utf8_strdown(nz(c->email),-1);
      gboolean ok = (strstr(name,f) || strstr(phone,f) || strstr(email,f));
      g_free(f); g_free(name); g_free(phone); g_free(email);
      if (!ok) continue;
    }
    clients_store_add(app,c);
  }
}

static void load_all_clients(App *app) {
  if (!app) return;
  if (!app->clients) app->clients = g_ptr_array_new_with_free_func((GDestroyNotify)client_free);
  else g_ptr_array_set_size(app->clients, 0);

  GDir *d = g_dir_open(CLIENTS_DIR, 0, NULL);
  if (d) {
    const gchar *name=NULL;
    while ((name = g_dir_read_name(d)) != NULL) {
      gchar *dir = g_build_filename(CLIENTS_DIR, name, NULL);
      if (g_file_test(dir, G_FILE_TEST_IS_DIR)) {
        Client *c = load_client_from_dir(dir);
        if (c && c->id && c->name) g_ptr_array_add(app->clients, c);
        else client_free(c);
      }
      g_free(dir);
    }
    g_dir_close(d);
  }

  if (app->c_store && app->c_search)
    clients_store_refresh(app, gtk_entry_get_text(app->c_search));

  app->current_client = NULL;
}

static void agenda_clients_combo_refresh(App *app) {
  if (!app || !app->a_client) return;

  gtk_combo_box_text_remove_all(app->a_client);
  gtk_combo_box_text_append(app->a_client, "", "— escolher —");

  if (!app->clients) {
    gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_client), 0);
    return;
  }

  for (guint i=0;i<app->clients->len;i++){
    Client *c = g_ptr_array_index(app->clients,i);
    if (!c || !c->id || !c->name) continue;
    gtk_combo_box_text_append(app->a_client, c->id, c->name); // sem ID visível
  }
  gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_client), 0);
}

// Não precisamos de combo específico para faturação (OPÇÃO 1),
// mas deixamos a função para não voltar a dar erro se chamares.
static void billing_clients_combo_refresh(App *app) { (void)app; }

/* ----------------- Save client (TXT + photos) ----------------- */
static gboolean save_client_to_disk(App *app,
                                    const gchar *id, const gchar *name,
                                    const gchar *phone,
                                    const gchar *email,
                                    const gchar *profession,
                                    const gchar *age,
                                    const gchar *notes,
                                    const gchar *picked_before,
                                    const gchar *picked_after)
{
  if (!app) return FALSE;
  if (!id || !*id) { msg(app->win, GTK_MESSAGE_WARNING, "Cliente", "Erro interno: ID vazio."); return FALSE; }
  if (!name || !*name) { msg(app->win, GTK_MESSAGE_WARNING, "Cliente", "Nome vazio."); return FALSE; }

  gchar *dir = client_dir_for_fields(id, name);
  if (g_mkdir_with_parents(dir, 0755) != 0 && errno != EEXIST) {
    msg(app->win, GTK_MESSAGE_ERROR, "Cliente", "Falha a criar pasta:\n%s", dir);
    g_free(dir); return FALSE;
  }

  gchar *before_path=NULL, *after_path=NULL;

  if (picked_before && *picked_before) {
    GError *err=NULL;
    gchar *dst = g_build_filename(dir, "antes.jpg", NULL);
    if (!copy_file_overwrite(picked_before, dst, &err)) {
      msg(app->win, GTK_MESSAGE_ERROR, "Fotos", "Falha copiar Antes:\n%s", err?err->message:"erro");
      if (err) g_error_free(err);
      g_free(dst); g_free(dir); return FALSE;
    }
    before_path = dst;
  }
  if (picked_after && *picked_after) {
    GError *err=NULL;
    gchar *dst = g_build_filename(dir, "depois.jpg", NULL);
    if (!copy_file_overwrite(picked_after, dst, &err)) {
      msg(app->win, GTK_MESSAGE_ERROR, "Fotos", "Falha copiar Depois:\n%s", err?err->message:"erro");
      if (err) g_error_free(err);
      g_free(dst); g_free(before_path); g_free(dir); return FALSE;
    }
    after_path = dst;
  }

  if (!before_path) {
    gchar *p = g_build_filename(dir, "antes.jpg", NULL);
    if (g_file_test(p, G_FILE_TEST_EXISTS)) before_path=p; else g_free(p);
  }
  if (!after_path) {
    gchar *p = g_build_filename(dir, "depois.jpg", NULL);
    if (g_file_test(p, G_FILE_TEST_EXISTS)) after_path=p; else g_free(p);
  }

  gchar *notes_esc = g_strescape(notes?notes:"", NULL);
  const char *rel_before = (before_path && g_file_test(before_path, G_FILE_TEST_EXISTS)) ? "antes.jpg" : "";
  const char *rel_after  = (after_path  && g_file_test(after_path,  G_FILE_TEST_EXISTS)) ? "depois.jpg" : "";

  gchar *content = g_strdup_printf(
    "id=%s\nnome=%s\ntelefone=%s\nemail=%s\nprofissao=%s\nidade=%s\nnotas=%s\nfoto_antes=%s\nfoto_depois=%s\n",
    id, name,
    phone?phone:"", email?email:"", profession?profession:"", age?age:"",
    notes_esc?notes_esc:"", rel_before, rel_after
  );

  gchar *txt = g_build_filename(dir, "cliente.txt", NULL);
  gboolean ok = g_file_set_contents(txt, content, -1, NULL);

  g_free(notes_esc); g_free(content); g_free(txt);
  g_free(before_path); g_free(after_path); g_free(dir);

  if (!ok) { msg(app->win, GTK_MESSAGE_ERROR, "Cliente", "Não consegui gravar cliente.txt"); return FALSE; }
  return TRUE;
}

/* ----------------- delete dir recursively ----------------- */
static gboolean delete_dir_recursive(const gchar *path) {
  if (!g_file_test(path, G_FILE_TEST_EXISTS)) return TRUE;
  GDir *d = g_dir_open(path, 0, NULL);
  if (!d) return FALSE;

  const gchar *name=NULL;
  while ((name = g_dir_read_name(d)) != NULL) {
    gchar *p = g_build_filename(path, name, NULL);
    if (g_file_test(p, G_FILE_TEST_IS_DIR)) {
      delete_dir_recursive(p);
      g_rmdir(p);
    } else g_remove(p);
    g_free(p);
  }
  g_dir_close(d);
  return TRUE;
}

/* ----------------- Agenda store ----------------- */
enum {
  A_COL_ID=0,
  A_COL_TIME,
  A_COL_DUR,
  A_COL_CLIENT_ID,
  A_COL_CLIENT,
  A_COL_SERVICE,
  A_COL_BARBER,
  A_COL_STATUS,
  A_COL_NOTES,
  A_NCOLS
};

static gchar* booking_new_id(void) {
  gint64 t = g_get_real_time();
  return g_strdup_printf("%" G_GINT64_FORMAT, t);
}

static void agenda_store_clear(App *app) { if (app && app->a_store) gtk_list_store_clear(app->a_store); }

static void agenda_store_add_row(App *app,
                                const gchar *id,
                                const gchar *time,
                                const gchar *dur,
                                const gchar *client_id,
                                const gchar *client,
                                const gchar *service,
                                const gchar *barber,
                                const gchar *status,
                                const gchar *notes)
{
  if (!app || !app->a_store) return;
  GtkTreeIter it; gtk_list_store_append(app->a_store,&it);
  gtk_list_store_set(app->a_store,&it,
                     A_COL_ID, nz(id),
                     A_COL_TIME, nz(time),
                     A_COL_DUR, nz(dur),
                     A_COL_CLIENT_ID, nz(client_id),
                     A_COL_CLIENT, nz(client),
                     A_COL_SERVICE, nz(service),
                     A_COL_BARBER, nz(barber),
                     A_COL_STATUS, nz(status),
                     A_COL_NOTES, nz(notes),
                     -1);
}

static gchar* line_get_val(const gchar *token, const gchar *key) {
  if (!token || !key) return NULL;
  gsize klen = strlen(key);
  if (g_str_has_prefix(token, key) && token[klen]=='=') return g_strdup(token+klen+1);
  return NULL;
}

static void agenda_load_file(App *app, const gchar *file) {
  if (!app || !file) return;
  agenda_store_clear(app);

  gchar *contents=NULL; gsize len=0;
  if (!g_file_get_contents(file,&contents,&len,NULL) || !contents) return;

  gchar **lines = g_strsplit(contents,"\n",-1);
  for (int i=0; lines && lines[i]; i++) {
    if (!lines[i][0]) continue;

    gchar *id=NULL,*time=NULL,*dur=NULL,*client_id=NULL,*client=NULL,*service=NULL,*barber=NULL,*status=NULL,*notes=NULL;
    gchar **parts = g_strsplit(lines[i],"|",-1);
    for (int p=0; parts && parts[p]; p++) {
      if (!id)        id        = line_get_val(parts[p],"id");
      if (!time)      time      = line_get_val(parts[p],"time");
      if (!dur)       dur       = line_get_val(parts[p],"dur");
      if (!client_id) client_id = line_get_val(parts[p],"client_id");
      if (!client)    client    = line_get_val(parts[p],"client");
      if (!service)   service   = line_get_val(parts[p],"service");
      if (!barber)    barber    = line_get_val(parts[p],"barber");
      if (!status)    status    = line_get_val(parts[p],"status");
      if (!notes)     notes     = line_get_val(parts[p],"notes");
    }
    if (notes) { gchar *un = g_strcompress(notes); g_free(notes); notes = un?un:g_strdup(""); }
    agenda_store_add_row(app,id,time,dur,client_id,client,service,barber,status,notes);
    g_free(id); g_free(time); g_free(dur); g_free(client_id); g_free(client);
    g_free(service); g_free(barber); g_free(status); g_free(notes);
    g_strfreev(parts);
  }
  g_strfreev(lines); g_free(contents);
  if (app->a_filter) gtk_tree_model_filter_refilter(app->a_filter);
}

static gboolean agenda_save_file_from_store(App *app, const gchar *file) {
  if (!app || !app->a_store || !file) return FALSE;
  GString *out = g_string_new("");

  GtkTreeIter it;
  gboolean valid = gtk_tree_model_get_iter_first(GTK_TREE_MODEL(app->a_store), &it);
  while (valid) {
    gchar *id=NULL,*time=NULL,*dur=NULL,*client_id=NULL,*client=NULL,*service=NULL,*barber=NULL,*status=NULL,*notes=NULL;
    gtk_tree_model_get(GTK_TREE_MODEL(app->a_store), &it,
                       A_COL_ID,&id, A_COL_TIME,&time, A_COL_DUR,&dur,
                       A_COL_CLIENT_ID,&client_id, A_COL_CLIENT,&client,
                       A_COL_SERVICE,&service, A_COL_BARBER,&barber,
                       A_COL_STATUS,&status, A_COL_NOTES,&notes, -1);

    gchar *notes_esc = g_strescape(notes?notes:"", NULL);
    g_string_append_printf(out,
      "id=%s|time=%s|dur=%s|client_id=%s|client=%s|service=%s|barber=%s|status=%s|notes=%s\n",
      nz(id), nz(time), nz(dur), nz(client_id), nz(client),
      nz(service), nz(barber), nz(status), notes_esc?notes_esc:""
    );

    g_free(notes_esc);
    g_free(id); g_free(time); g_free(dur); g_free(client_id); g_free(client);
    g_free(service); g_free(barber); g_free(status); g_free(notes);

    valid = gtk_tree_model_iter_next(GTK_TREE_MODEL(app->a_store), &it);
  }

  gboolean ok = g_file_set_contents(file, out->str, -1, NULL);
  g_string_free(out, TRUE);
  return ok;
}

/* ----------------- Email selected booking ----------------- */
static void copy_to_clipboard(const gchar *text) {
  GtkClipboard *cb = gtk_clipboard_get(GDK_SELECTION_CLIPBOARD);
  if (cb) gtk_clipboard_set_text(cb, text?text:"", -1);
}

static gboolean open_email_default_app(GtkWindow *parent, const gchar *to,
                                      const gchar *subject, const gchar *body)
{
  if (!to || !*to) return FALSE;
  gchar *sub_esc  = g_uri_escape_string(subject?subject:"", NULL, TRUE);
  gchar *body_esc = g_uri_escape_string(body?body:"", NULL, TRUE);
  gchar *mailto = g_strdup_printf("mailto:%s?subject=%s&body=%s", to, sub_esc, body_esc);

  GError *err=NULL;
  gboolean ok = g_app_info_launch_default_for_uri(mailto, NULL, &err);
  if (!ok) {
    copy_to_clipboard(mailto);
    msg(parent, GTK_MESSAGE_WARNING, "Email",
        "Não consegui abrir o cliente de email.\nCopiei para a clipboard um mailto:\n\n%s\n\nErro:\n%s",
        mailto, err?err->message:"erro");
  }
  if (err) g_error_free(err);
  g_free(sub_esc); g_free(body_esc); g_free(mailto);
  return ok;
}

/* ----------------- Agenda filters ----------------- */
static gboolean agenda_filter_visible(GtkTreeModel *model, GtkTreeIter *iter, gpointer user_data) {
  App *app = (App*)user_data;
  gchar *barber=NULL, *status=NULL;
  gtk_tree_model_get(model, iter, A_COL_BARBER, &barber, A_COL_STATUS, &status, -1);

  gboolean ok = TRUE;
  if (app->filter_barber && *app->filter_barber)
    ok = ok && (g_strcmp0(barber, app->filter_barber) == 0);
  if (app->filter_status && *app->filter_status)
    ok = ok && (g_strcmp0(status, app->filter_status) == 0);

  g_free(barber); g_free(status);
  return ok;
}
static void agenda_refilter(App *app) { if (app && app->a_filter) gtk_tree_model_filter_refilter(app->a_filter); }

/* ----------------- Agenda: conflict detection ----------------- */
static gboolean validate_time_format(const char *s) {
  if (!s || strlen(s)!=5) return FALSE;
  if (s[2] != ':') return FALSE;
  if (!g_ascii_isdigit(s[0])||!g_ascii_isdigit(s[1])||!g_ascii_isdigit(s[3])||!g_ascii_isdigit(s[4])) return FALSE;
  int hh = (s[0]-'0')*10 + (s[1]-'0');
  int mm = (s[3]-'0')*10 + (s[4]-'0');
  return (hh>=0 && hh<=23 && mm>=0 && mm<=59);
}
static int time_to_minutes(const char *hhmm) {
  if (!validate_time_format(hhmm)) return -1;
  int hh = (hhmm[0]-'0')*10 + (hhmm[1]-'0');
  int mm = (hhmm[3]-'0')*10 + (hhmm[4]-'0');
  return hh*60 + mm;
}
static gboolean overlaps(int s1, int e1, int s2, int e2) { return (s1 < e2) && (s2 < e1); }

static gboolean agenda_has_conflict(App *app,
                                   const gchar *new_time,
                                   int new_dur,
                                   const gchar *new_barber,
                                   const gchar *ignore_booking_id,
                                   gchar **out_conflict_desc)
{
  if (!app || !app->a_store || !new_time || !new_barber) return FALSE;

  int ns = time_to_minutes(new_time);
  if (ns < 0) return FALSE;
  int ne = ns + (new_dur>0?new_dur:0);

  GtkTreeIter it;
  gboolean valid = gtk_tree_model_get_iter_first(GTK_TREE_MODEL(app->a_store), &it);
  while (valid) {
    gchar *id=NULL,*time=NULL,*dur=NULL,*barber=NULL,*client=NULL;
    gtk_tree_model_get(GTK_TREE_MODEL(app->a_store), &it,
                       A_COL_ID,&id, A_COL_TIME,&time, A_COL_DUR,&dur,
                       A_COL_BARBER,&barber, A_COL_CLIENT,&client, -1);

    gboolean same = (barber && g_strcmp0(barber, new_barber)==0);
    gboolean ignore = (ignore_booking_id && id && g_strcmp0(id, ignore_booking_id)==0);

    if (same && !ignore && validate_time_format(time)) {
      int os = time_to_minutes(time);
      int od = dur ? (int)g_ascii_strtoll(dur,NULL,10) : 0;
      int oe = os + (od>0?od:0);

      if (overlaps(ns, ne, os, oe)) {
        if (out_conflict_desc) {
          *out_conflict_desc = g_strdup_printf("Conflito com %s — %s (%d min) [%s]",
                                               nz(client), nz(time), od, nz(barber));
        }
        g_free(id); g_free(time); g_free(dur); g_free(barber); g_free(client);
        return TRUE;
      }
    }

    g_free(id); g_free(time); g_free(dur); g_free(barber); g_free(client);
    valid = gtk_tree_model_iter_next(GTK_TREE_MODEL(app->a_store), &it);
  }
  return FALSE;
}

/* ----------------- Widgets helpers ----------------- */
static GtkWidget* labeled(GtkWidget *w, const char *label) {
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
  GtkWidget *l = gtk_label_new(label);
  gtk_label_set_xalign(GTK_LABEL(l), 0.0);
  gtk_box_pack_start(GTK_BOX(box), l, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(box), w, FALSE, FALSE, 0);
  return box;
}

/* ----------------- Clients UI actions ----------------- */
typedef struct { App *app; gboolean is_before; } PhotoCtx;

static void client_form_clear(App *app) {
  if (!app) return;
  gtk_entry_set_text(app->c_name,"");
  gtk_entry_set_text(app->c_phone,"");
  gtk_entry_set_text(app->c_email,"");
  gtk_entry_set_text(app->c_profession,"");
  gtk_entry_set_text(app->c_age,"");
  set_textview_text(app->c_notes,"");
  g_clear_pointer(&app->picked_before_src,g_free);
  g_clear_pointer(&app->picked_after_src,g_free);
  app->current_client=NULL;
  set_image_from_path(app->c_img_before,NULL);
  set_image_from_path(app->c_img_after,NULL);
}

static void client_form_fill(App *app, Client *c) {
  if (!app || !c) return;
  app->current_client = c;
  gtk_entry_set_text(app->c_name, nz(c->name));
  gtk_entry_set_text(app->c_phone,nz(c->phone));
  gtk_entry_set_text(app->c_email,nz(c->email));
  gtk_entry_set_text(app->c_profession,nz(c->profession));
  gtk_entry_set_text(app->c_age,nz(c->age));
  set_textview_text(app->c_notes, nz(c->notes));
  set_image_from_path(app->c_img_before, c->before_path);
  set_image_from_path(app->c_img_after,  c->after_path);
}

static void on_client_search_changed(GtkEntry *e, gpointer user_data) {
  App *app = (App*)user_data;
  if (!app) return;
  clients_store_refresh(app, gtk_entry_get_text(e));
}

static void on_client_list_selection(GtkTreeSelection *sel, gpointer user_data) {
  App *app = (App*)user_data;
  if (!app) return;
  GtkTreeModel *model=NULL; GtkTreeIter it;
  if (!gtk_tree_selection_get_selected(sel,&model,&it)) return;
  gchar *id=NULL; gtk_tree_model_get(model,&it,C_COL_ID,&id,-1);
  if (!id) return;
  Client *c = client_find_by_id(app,id);
  if (c) client_form_fill(app,c);
  g_free(id);
}

static void on_client_photo_load(GtkButton *b, gpointer user_data) {
  (void)b;
  PhotoCtx *ctx = (PhotoCtx*)user_data;
  App *app = ctx->app;

  GtkWidget *dlg = gtk_file_chooser_dialog_new(
    ctx->is_before ? "Escolher Foto Antes" : "Escolher Foto Depois",
    app->win, GTK_FILE_CHOOSER_ACTION_OPEN,
    "_Cancelar", GTK_RESPONSE_CANCEL,
    "_Abrir", GTK_RESPONSE_ACCEPT, NULL
  );

  GtkFileFilter *f = gtk_file_filter_new();
  gtk_file_filter_set_name(f,"Imagens (JPG/PNG)");
  gtk_file_filter_add_mime_type(f,"image/jpeg");
  gtk_file_filter_add_mime_type(f,"image/png");
  gtk_file_chooser_add_filter(GTK_FILE_CHOOSER(dlg), f);

  if (gtk_dialog_run(GTK_DIALOG(dlg)) == GTK_RESPONSE_ACCEPT) {
    char *src = gtk_file_chooser_get_filename(GTK_FILE_CHOOSER(dlg));
    if (ctx->is_before) {
      g_free(app->picked_before_src);
      app->picked_before_src = g_strdup(src);
      set_image_from_path(app->c_img_before, src);
    } else {
      g_free(app->picked_after_src);
      app->picked_after_src = g_strdup(src);
      set_image_from_path(app->c_img_after, src);
    }
    g_free(src);
  }
  gtk_widget_destroy(dlg);
}

static void on_client_photo_view(GtkButton *b, gpointer user_data) {
  (void)b;
  PhotoCtx *ctx = (PhotoCtx*)user_data;
  App *app = ctx->app;
  const gchar *path = NULL;
  if (ctx->is_before) {
    path = app->picked_before_src ? app->picked_before_src :
           (app->current_client ? app->current_client->before_path : NULL);
    open_image_viewer(app->win, path, "Foto Antes");
  } else {
    path = app->picked_after_src ? app->picked_after_src :
           (app->current_client ? app->current_client->after_path : NULL);
    open_image_viewer(app->win, path, "Foto Depois");
  }
}

static void on_client_photo_remove(GtkButton *b, gpointer user_data) {
  (void)b;
  PhotoCtx *ctx = (PhotoCtx*)user_data;
  App *app = ctx->app;

  if (ctx->is_before) {
    g_clear_pointer(&app->picked_before_src, g_free);
    if (app->current_client && app->current_client->before_path &&
        g_file_test(app->current_client->before_path, G_FILE_TEST_EXISTS)) {
      g_remove(app->current_client->before_path);
      g_clear_pointer(&app->current_client->before_path, g_free);
    }
    set_image_from_path(app->c_img_before,NULL);
  } else {
    g_clear_pointer(&app->picked_after_src, g_free);
    if (app->current_client && app->current_client->after_path &&
        g_file_test(app->current_client->after_path, G_FILE_TEST_EXISTS)) {
      g_remove(app->current_client->after_path);
      g_clear_pointer(&app->current_client->after_path, g_free);
    }
    set_image_from_path(app->c_img_after,NULL);
  }
}

static void on_client_new(GtkButton *b, gpointer user_data) {
  (void)b;
  App *app = (App*)user_data;
  client_form_clear(app);
  gtk_widget_grab_focus(GTK_WIDGET(app->c_name));
}

static void on_client_save(GtkButton *b, gpointer user_data) {
  (void)b;
  App *app = (App*)user_data;

  const gchar *name = gtk_entry_get_text(app->c_name);
  const gchar *phone= gtk_entry_get_text(app->c_phone);
  const gchar *email= gtk_entry_get_text(app->c_email);
  const gchar *prof = gtk_entry_get_text(app->c_profession);
  const gchar *age  = gtk_entry_get_text(app->c_age);
  gchar *notes = get_textview_text(app->c_notes);

  gchar *id=NULL;
  if (app->current_client && app->current_client->id && *app->current_client->id)
    id = g_strdup(app->current_client->id);
  else
    id = next_client_id(app->win);

  gboolean ok = save_client_to_disk(app, id, name, phone, email, prof, age, notes,
                                   app->picked_before_src, app->picked_after_src);
  g_free(notes);
  if (!ok) { g_free(id); return; }

  g_clear_pointer(&app->picked_before_src,g_free);
  g_clear_pointer(&app->picked_after_src,g_free);

  load_all_clients(app);
  agenda_clients_combo_refresh(app);
  billing_clients_combo_refresh(app);

  Client *c2 = client_find_by_id(app,id);
  if (c2) client_form_fill(app,c2); else client_form_clear(app);

  msg(app->win, GTK_MESSAGE_INFO, "Cliente", "Cliente guardado.");
  g_free(id);
}

static void on_client_delete(GtkButton *b, gpointer user_data) {
  (void)b;
  App *app = (App*)user_data;

  if (!app->current_client || !app->current_client->id) {
    msg(app->win, GTK_MESSAGE_INFO, "Cliente", "Seleciona um cliente primeiro.");
    return;
  }
  Client *c = app->current_client;
  if (!c->dir) { msg(app->win, GTK_MESSAGE_INFO, "Cliente", "Cliente não encontrado no disco."); return; }

  if (!confirm_yes_no(app->win, "Confirmar", "Apagar o cliente %s?", nz(c->name))) return;

  delete_dir_recursive(c->dir);
  g_rmdir(c->dir);

  client_form_clear(app);
  load_all_clients(app);
  agenda_clients_combo_refresh(app);
  billing_clients_combo_refresh(app);

  msg(app->win, GTK_MESSAGE_INFO, "Cliente", "Cliente apagado.");
}

/* ----------------- Agenda selection helpers ----------------- */
static gboolean agenda_get_selected_child_iter(App *app, GtkTreeIter *out_child) {
  if (!app || !app->a_list) return FALSE;
  GtkTreeSelection *sel = gtk_tree_view_get_selection(app->a_list);
  GtkTreeModel *model=NULL; GtkTreeIter it;
  if (!gtk_tree_selection_get_selected(sel,&model,&it)) return FALSE;

  if (GTK_IS_TREE_MODEL_FILTER(model) && app->a_filter) {
    GtkTreeIter child;
    gtk_tree_model_filter_convert_iter_to_child_iter(app->a_filter,&child,&it);
    if (out_child) *out_child = child;
    return TRUE;
  }
  if (out_child) *out_child = it;
  return TRUE;
}

/* ----------------- Agenda UI actions ----------------- */
static void agenda_form_clear(App *app) {
  gtk_entry_set_text(app->a_time,"");
  gtk_spin_button_set_value(app->a_dur,30);
  gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_client),0);
  gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_service),0);
  gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_barber),0);
  gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_status),0);
  set_textview_text(app->a_notes,"");
}

static void agenda_fill_form_from_child_iter(App *app, GtkTreeIter *it) {
  gchar *time=NULL,*dur=NULL,*client_id=NULL,*service=NULL,*barber=NULL,*status=NULL,*notes=NULL;
  gtk_tree_model_get(GTK_TREE_MODEL(app->a_store), it,
                     A_COL_TIME,&time, A_COL_DUR,&dur, A_COL_CLIENT_ID,&client_id,
                     A_COL_SERVICE,&service, A_COL_BARBER,&barber, A_COL_STATUS,&status,
                     A_COL_NOTES,&notes, -1);

  gtk_entry_set_text(app->a_time, time?time:"");
  gtk_spin_button_set_value(app->a_dur, dur?g_ascii_strtoll(dur,NULL,10):30);

  if (client_id && *client_id) gtk_combo_box_set_active_id(GTK_COMBO_BOX(app->a_client), client_id);
  else gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_client), 0);

  // match combos by text
  {
    int n = gtk_tree_model_iter_n_children(gtk_combo_box_get_model(GTK_COMBO_BOX(app->a_service)), NULL);
    for (int i=0;i<n;i++){
      gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_service), i);
      gchar *t = gtk_combo_box_text_get_active_text(app->a_service);
      gboolean ok = (t && service && g_strcmp0(t, service)==0);
      g_free(t);
      if (ok) break;
    }
  }
  {
    int n = gtk_tree_model_iter_n_children(gtk_combo_box_get_model(GTK_COMBO_BOX(app->a_barber)), NULL);
    for (int i=0;i<n;i++){
      gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_barber), i);
      gchar *t = gtk_combo_box_text_get_active_text(app->a_barber);
      gboolean ok = (t && barber && g_strcmp0(t, barber)==0);
      g_free(t);
      if (ok) break;
    }
  }
  {
    int n = gtk_tree_model_iter_n_children(gtk_combo_box_get_model(GTK_COMBO_BOX(app->a_status)), NULL);
    for (int i=0;i<n;i++){
      gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_status), i);
      gchar *t = gtk_combo_box_text_get_active_text(app->a_status);
      gboolean ok = (t && status && g_strcmp0(t, status)==0);
      g_free(t);
      if (ok) break;
    }
  }

  set_textview_text(app->a_notes, notes?notes:"");

  g_free(time); g_free(dur); g_free(client_id); g_free(service); g_free(barber); g_free(status); g_free(notes);
}

static void on_agenda_selection_changed(GtkTreeSelection *sel, gpointer user_data) {
  (void)sel;
  App *app = (App*)user_data;
  GtkTreeIter child;
  if (!agenda_get_selected_child_iter(app,&child)) return;
  agenda_fill_form_from_child_iter(app,&child);
}

static void on_agenda_calendar_changed(GtkCalendar *cal, gpointer user_data) {
  (void)cal;
  App *app = (App*)user_data;
  gchar *file = agenda_file_for_calendar(app->a_cal);
  g_free(app->agenda_file_loaded);
  app->agenda_file_loaded = g_strdup(file);
  agenda_load_file(app,file);
  agenda_form_clear(app);
  g_free(file);
}

static void on_agenda_new(GtkButton *b, gpointer user_data) {
  (void)b;
  App *app = (App*)user_data;
  agenda_form_clear(app);
  gtk_widget_grab_focus(GTK_WIDGET(app->a_time));
}

static void on_agenda_add(GtkButton *b, gpointer user_data) {
  (void)b;
  App *app = (App*)user_data;

  const gchar *time = gtk_entry_get_text(app->a_time);
  if (!validate_time_format(time)) { msg(app->win, GTK_MESSAGE_WARNING, "Agenda", "Hora inválida. Usa HH:MM."); return; }
  int dur = (int)gtk_spin_button_get_value(app->a_dur);

  const gchar *client_id = gtk_combo_box_get_active_id(GTK_COMBO_BOX(app->a_client));
  if (!client_id || !*client_id || gtk_combo_box_get_active(GTK_COMBO_BOX(app->a_client))==0) {
    msg(app->win, GTK_MESSAGE_WARNING, "Agenda", "Escolhe um cliente.");
    return;
  }

  Client *c = client_find_by_id(app, client_id);
  const gchar *client_name = c ? c->name : "";

  gchar *service = gtk_combo_box_text_get_active_text(app->a_service);
  gchar *barber  = gtk_combo_box_text_get_active_text(app->a_barber);
  gchar *status  = gtk_combo_box_text_get_active_text(app->a_status);
  gchar *notes   = get_textview_text(app->a_notes);

  gchar *conf=NULL;
  if (barber && *barber && agenda_has_conflict(app, time, dur, barber, NULL, &conf)) {
    gboolean go = confirm_yes_no(app->win, "Conflito de horário",
                                "Já existe marcação que choca.\n\n%s\n\nGravar na mesma?",
                                conf?conf:"(conflito)");
    g_free(conf);
    if (!go) { g_free(service); g_free(barber); g_free(status); g_free(notes); return; }
  }

  gchar *id = booking_new_id();
  gchar *dur_s = g_strdup_printf("%d", dur);

  agenda_store_add_row(app, id, time, dur_s, client_id, client_name,
                      service?service:"", barber?barber:"", status?status:"Marcado",
                      notes?notes:"");

  if (app->agenda_file_loaded) agenda_save_file_from_store(app, app->agenda_file_loaded);
  agenda_refilter(app);

  g_free(id); g_free(dur_s);
  g_free(service); g_free(barber); g_free(status); g_free(notes);

  agenda_form_clear(app);
}

static void on_agenda_update(GtkButton *b, gpointer user_data) {
  (void)b;
  App *app = (App*)user_data;
  GtkTreeIter it;
  if (!agenda_get_selected_child_iter(app,&it)) { msg(app->win, GTK_MESSAGE_INFO, "Agenda", "Seleciona uma marcação."); return; }

  gchar *cur_id=NULL; gtk_tree_model_get(GTK_TREE_MODEL(app->a_store), &it, A_COL_ID, &cur_id, -1);

  const gchar *time = gtk_entry_get_text(app->a_time);
  if (!validate_time_format(time)) { g_free(cur_id); msg(app->win, GTK_MESSAGE_WARNING, "Agenda", "Hora inválida. Usa HH:MM."); return; }

  int dur = (int)gtk_spin_button_get_value(app->a_dur);
  gchar *dur_s = g_strdup_printf("%d", dur);

  const gchar *client_id = gtk_combo_box_get_active_id(GTK_COMBO_BOX(app->a_client));
  if (!client_id || !*client_id || gtk_combo_box_get_active(GTK_COMBO_BOX(app->a_client))==0) {
    g_free(dur_s); g_free(cur_id);
    msg(app->win, GTK_MESSAGE_WARNING, "Agenda", "Escolhe um cliente.");
    return;
  }

  Client *c = client_find_by_id(app, client_id);
  const gchar *client_name = c ? c->name : "";

  gchar *service = gtk_combo_box_text_get_active_text(app->a_service);
  gchar *barber  = gtk_combo_box_text_get_active_text(app->a_barber);
  gchar *status  = gtk_combo_box_text_get_active_text(app->a_status);
  gchar *notes   = get_textview_text(app->a_notes);

  gchar *conf=NULL;
  if (barber && *barber && agenda_has_conflict(app, time, dur, barber, cur_id, &conf)) {
    gboolean go = confirm_yes_no(app->win, "Conflito de horário",
                                "Já existe marcação que choca.\n\n%s\n\nGravar na mesma?",
                                conf?conf:"(conflito)");
    g_free(conf);
    if (!go) {
      g_free(dur_s); g_free(service); g_free(barber); g_free(status); g_free(notes); g_free(cur_id);
      return;
    }
  }

  gtk_list_store_set(app->a_store, &it,
                     A_COL_TIME, time,
                     A_COL_DUR, dur_s,
                     A_COL_CLIENT_ID, client_id,
                     A_COL_CLIENT, client_name,
                     A_COL_SERVICE, service?service:"",
                     A_COL_BARBER, barber?barber:"",
                     A_COL_STATUS, status?status:"",
                     A_COL_NOTES, notes?notes:"",
                     -1);

  if (app->agenda_file_loaded) agenda_save_file_from_store(app, app->agenda_file_loaded);
  agenda_refilter(app);

  g_free(cur_id); g_free(dur_s);
  g_free(service); g_free(barber); g_free(status); g_free(notes);
}

static void on_agenda_delete(GtkButton *b, gpointer user_data) {
  (void)b;
  App *app = (App*)user_data;
  GtkTreeIter it;
  if (!agenda_get_selected_child_iter(app,&it)) { msg(app->win, GTK_MESSAGE_INFO, "Agenda", "Seleciona uma marcação."); return; }
  gtk_list_store_remove(app->a_store, &it);
  if (app->agenda_file_loaded) agenda_save_file_from_store(app, app->agenda_file_loaded);
  agenda_refilter(app);
  agenda_form_clear(app);
}

static void on_agenda_save_day(GtkButton *b, gpointer user_data) {
  (void)b;
  App *app = (App*)user_data;
  if (!app->agenda_file_loaded) { msg(app->win, GTK_MESSAGE_WARNING, "Agenda", "Ainda não há dia carregado."); return; }
  if (!agenda_save_file_from_store(app, app->agenda_file_loaded)) { msg(app->win, GTK_MESSAGE_ERROR, "Agenda", "Falha a gravar o ficheiro."); return; }
  msg(app->win, GTK_MESSAGE_INFO, "Agenda", "Dia guardado.");
}

/* ----------------- Email selected booking ----------------- */
static gboolean agenda_get_selected_child_iter(App *app, GtkTreeIter *out_child);

static void on_agenda_email_selected(GtkButton *b, gpointer user_data) {
  (void)b;
  App *app = (App*)user_data;

  GtkTreeIter it;
  if (!agenda_get_selected_child_iter(app, &it)) {
    msg(app->win, GTK_MESSAGE_INFO, "Email", "Seleciona uma marcação na lista.");
    return;
  }

  gchar *time=NULL,*client_id=NULL,*service=NULL;
  gtk_tree_model_get(GTK_TREE_MODEL(app->a_store), &it,
                     A_COL_TIME,&time, A_COL_CLIENT_ID,&client_id, A_COL_SERVICE,&service, -1);

  Client *c = client_find_by_id(app, client_id);
  if (!c || !c->email || !*c->email) {
    msg(app->win, GTK_MESSAGE_INFO, "Email", "Este cliente não tem email.");
    g_free(time); g_free(client_id); g_free(service);
    return;
  }

  gchar *date = calendar_to_date_str(app->a_cal);
  gchar *subject = g_strdup_printf("Marcacao Barbearia Neves - %s %s", nz(date), nz(time));
  gchar *body = g_strdup_printf(
    "Ola %s,\n\n"
    "A sua marcacao:\n"
    "- Data: %s\n"
    "- Hora: %s\n"
    "- Servico: %s\n\n"
    "Obrigado!\n"
    "Barbearia Neves\n",
    nz(c->name), nz(date), nz(time), nz(service)
  );

  open_email_default_app(app->win, c->email, subject, body);

  g_free(date); g_free(subject); g_free(body);
  g_free(time); g_free(client_id); g_free(service);
}

/* ----------------- Filters callbacks ----------------- */
static void on_filter_barber_changed(GtkComboBox *cb, gpointer user_data) {
  App *app = (App*)user_data;
  gchar *t = gtk_combo_box_text_get_active_text(GTK_COMBO_BOX_TEXT(cb));
  g_clear_pointer(&app->filter_barber, g_free);
  if (t && g_strcmp0(t,"Todos")!=0) app->filter_barber = g_strdup(t);
  else app->filter_barber = NULL;
  g_free(t);
  agenda_refilter(app);
}

static void on_filter_status_changed(GtkComboBox *cb, gpointer user_data) {
  App *app = (App*)user_data;
  gchar *t = gtk_combo_box_text_get_active_text(GTK_COMBO_BOX_TEXT(cb));
  g_clear_pointer(&app->filter_status, g_free);
  if (t && g_strcmp0(t,"Todos")!=0) app->filter_status = g_strdup(t);
  else app->filter_status = NULL;
  g_free(t);
  agenda_refilter(app);
}

/* ----------------- Double-click opens client ----------------- */
static gboolean select_client_in_list(App *app, const gchar *client_id) {
  if (!app || !app->c_list || !app->c_store || !client_id || !*client_id) return FALSE;
  GtkTreeModel *m = GTK_TREE_MODEL(app->c_store);
  GtkTreeIter it;
  gboolean valid = gtk_tree_model_get_iter_first(m, &it);
  while (valid) {
    gchar *id=NULL;
    gtk_tree_model_get(m,&it,C_COL_ID,&id,-1);
    gboolean match = (id && g_strcmp0(id, client_id)==0);
    g_free(id);
    if (match) {
      GtkTreeSelection *sel = gtk_tree_view_get_selection(app->c_list);
      GtkTreePath *path = gtk_tree_model_get_path(m,&it);
      gtk_tree_selection_select_iter(sel,&it);
      gtk_tree_view_scroll_to_cell(app->c_list, path, NULL, TRUE, 0.25, 0.0);
      gtk_tree_path_free(path);
      return TRUE;
    }
    valid = gtk_tree_model_iter_next(m,&it);
  }
  return FALSE;
}

static void on_agenda_row_activated(GtkTreeView *tv, GtkTreePath *path, GtkTreeViewColumn *col, gpointer user_data) {
  (void)tv; (void)col;
  App *app = (App*)user_data;
  if (!app || !app->nb) return;

  GtkTreeIter fit;
  if (!gtk_tree_model_get_iter(GTK_TREE_MODEL(app->a_filter), &fit, path)) return;

  GtkTreeIter child;
  gtk_tree_model_filter_convert_iter_to_child_iter(app->a_filter, &child, &fit);

  gchar *client_id=NULL;
  gtk_tree_model_get(GTK_TREE_MODEL(app->a_store), &child, A_COL_CLIENT_ID, &client_id, -1);
  if (!client_id || !*client_id) { g_free(client_id); return; }

  gtk_notebook_set_current_page(app->nb, app->page_clients);
  clients_store_refresh(app, gtk_entry_get_text(app->c_search));
  select_client_in_list(app, client_id);

  Client *c = client_find_by_id(app, client_id);
  if (c) client_form_fill(app,c);

  g_free(client_id);
}

/* ----------------- PDF export ----------------- */
static void cairo_draw_text(cairo_t *cr, const char *font, double size_pt, double x, double y, const char *text) {
  cairo_save(cr);
  cairo_select_font_face(cr, font, CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_NORMAL);
  cairo_set_font_size(cr, size_pt);
  cairo_move_to(cr, x, y);
  cairo_show_text(cr, text?text:"");
  cairo_restore(cr);
}

static void export_agenda_day_to_pdf(App *app) {
  if (!app) return;

  gchar *date_ymd = calendar_to_date_str(app->a_cal);
  gchar *default_name = g_strdup_printf("agenda_%s.pdf", date_ymd);

  GtkWidget *dlg = gtk_file_chooser_dialog_new("Exportar PDF do dia",
    app->win, GTK_FILE_CHOOSER_ACTION_SAVE,
    "_Cancelar", GTK_RESPONSE_CANCEL,
    "_Guardar", GTK_RESPONSE_ACCEPT, NULL);
  gtk_file_chooser_set_do_overwrite_confirmation(GTK_FILE_CHOOSER(dlg), TRUE);
  gtk_file_chooser_set_current_name(GTK_FILE_CHOOSER(dlg), default_name);

  gboolean ok=FALSE; gchar *out_path=NULL;
  if (gtk_dialog_run(GTK_DIALOG(dlg)) == GTK_RESPONSE_ACCEPT) {
    out_path = gtk_file_chooser_get_filename(GTK_FILE_CHOOSER(dlg));
    ok=TRUE;
  }
  gtk_widget_destroy(dlg);
  g_free(default_name);

  if (!ok || !out_path) { g_free(date_ymd); g_free(out_path); return; }

  const double W=595.0, H=842.0, margin=40.0;
  cairo_surface_t *surface = cairo_pdf_surface_create(out_path, W, H);
  cairo_t *cr = cairo_create(surface);

  cairo_draw_text(cr,"Sans",18, margin, margin+10, "Barbearia Neves — Agenda do dia");
  gchar *hdr = g_strdup_printf("Data: %s", date_ymd);
  cairo_draw_text(cr,"Sans",13, margin, margin+34, hdr);
  g_free(hdr);

  double y = margin + 80;
  cairo_set_line_width(cr, 1.0);
  cairo_draw_text(cr,"Sans",11, margin, y, "Hora");
  cairo_draw_text(cr,"Sans",11, margin+70, y, "Cliente");
  cairo_draw_text(cr,"Sans",11, margin+300,y, "Serviço");
  cairo_draw_text(cr,"Sans",11, margin+410,y, "Barbeiro");
  cairo_draw_text(cr,"Sans",11, margin+490,y, "Estado");

  y += 10; cairo_move_to(cr, margin, y); cairo_line_to(cr, W-margin, y); cairo_stroke(cr);
  y += 18;

  GtkTreeIter it;
  gboolean valid = gtk_tree_model_get_iter_first(GTK_TREE_MODEL(app->a_store), &it);
  while (valid) {
    gchar *time=NULL,*client=NULL,*service=NULL,*barber=NULL,*status=NULL,*dur=NULL;
    gtk_tree_model_get(GTK_TREE_MODEL(app->a_store), &it,
                       A_COL_TIME,&time, A_COL_CLIENT,&client, A_COL_SERVICE,&service,
                       A_COL_BARBER,&barber, A_COL_STATUS,&status, A_COL_DUR,&dur, -1);

    if (y > H - margin) {
      cairo_show_page(cr);
      y = margin + 20;
      cairo_draw_text(cr,"Sans",16, margin, y, "Barbearia Neves — Agenda do dia (continuação)");
      gchar *hdr2 = g_strdup_printf("Data: %s", date_ymd);
      cairo_draw_text(cr,"Sans",12, margin, y+20, hdr2);
      g_free(hdr2);
      y += 60;
      cairo_draw_text(cr,"Sans",11, margin, y, "Hora");
      cairo_draw_text(cr,"Sans",11, margin+70, y, "Cliente");
      cairo_draw_text(cr,"Sans",11, margin+300,y, "Serviço");
      cairo_draw_text(cr,"Sans",11, margin+410,y, "Barbeiro");
      cairo_draw_text(cr,"Sans",11, margin+490,y, "Estado");
      y += 10; cairo_move_to(cr, margin, y); cairo_line_to(cr, W-margin, y); cairo_stroke(cr);
      y += 18;
    }

    gchar *svc = g_strdup_printf("%s (%s min)", nz(service), nz(dur));
    cairo_draw_text(cr,"Sans",10, margin, y, nz(time));
    cairo_draw_text(cr,"Sans",10, margin+70, y, nz(client));
    cairo_draw_text(cr,"Sans",10, margin+300,y, svc);
    cairo_draw_text(cr,"Sans",10, margin+410,y, nz(barber));
    cairo_draw_text(cr,"Sans",10, margin+490,y, nz(status));
    g_free(svc);

    y += 16;

    g_free(time); g_free(client); g_free(service); g_free(barber); g_free(status); g_free(dur);
    valid = gtk_tree_model_iter_next(GTK_TREE_MODEL(app->a_store), &it);
  }

  cairo_destroy(cr);
  cairo_surface_destroy(surface);

  msg(app->win, GTK_MESSAGE_INFO, "PDF", "PDF exportado:\n%s", out_path);

  g_free(out_path);
  g_free(date_ymd);
}

static void on_agenda_export_pdf(GtkButton *b, gpointer user_data) { (void)b; export_agenda_day_to_pdf((App*)user_data); }

/* ----------------- Billing (OPÇÃO 1) ----------------- */
/*
  faturacao.txt (1 linha = 1 item)
  booking_id=...|date=YYYY-MM-DD|time=HH:MM|client=...|service=...|barber=...|amount=15.00|paid=0|paid_amount=0.00|paid_date=
*/

enum {
  B_COL_BOOKING_ID=0, // hidden
  B_COL_DATE,
  B_COL_TIME,
  B_COL_CLIENT,
  B_COL_SERVICE,
  B_COL_BARBER,
  B_COL_AMOUNT,
  B_COL_PAID,        // "0"/"1"
  B_COL_PAID_AMOUNT, // "0.00"
  B_COL_PAID_DATE,   // "YYYY-MM-DD" opcional
  B_NCOLS
};

static gchar* b_line_get_val(const gchar *token, const gchar *key) { return line_get_val(token, key); }

static void billing_store_add(App *app,
                              const gchar *booking_id,
                              const gchar *date,
                              const gchar *time,
                              const gchar *client,
                              const gchar *service,
                              const gchar *barber,
                              const gchar *amount,
                              const gchar *paid,
                              const gchar *paid_amount,
                              const gchar *paid_date)
{
  if (!app || !app->b_store) return;
  GtkTreeIter it; gtk_list_store_append(app->b_store,&it);
  gtk_list_store_set(app->b_store,&it,
                     B_COL_BOOKING_ID, nz(booking_id),
                     B_COL_DATE, nz(date),
                     B_COL_TIME, nz(time),
                     B_COL_CLIENT, nz(client),
                     B_COL_SERVICE, nz(service),
                     B_COL_BARBER, nz(barber),
                     B_COL_AMOUNT, nz(amount),
                     B_COL_PAID, nz(paid),
                     B_COL_PAID_AMOUNT, nz(paid_amount),
                     B_COL_PAID_DATE, nz(paid_date),
                     -1);
}

static gboolean billing_save_file(App *app) {
  if (!app || !app->b_store) return FALSE;
  GString *out = g_string_new("");

  GtkTreeIter it;
  gboolean valid = gtk_tree_model_get_iter_first(GTK_TREE_MODEL(app->b_store), &it);
  while (valid) {
    gchar *booking_id=NULL,*date=NULL,*time=NULL,*client=NULL,*service=NULL,*barber=NULL,*amount=NULL,*paid=NULL,*paid_amount=NULL,*paid_date=NULL;
    gtk_tree_model_get(GTK_TREE_MODEL(app->b_store), &it,
      B_COL_BOOKING_ID,&booking_id, B_COL_DATE,&date, B_COL_TIME,&time,
      B_COL_CLIENT,&client, B_COL_SERVICE,&service, B_COL_BARBER,&barber,
      B_COL_AMOUNT,&amount, B_COL_PAID,&paid, B_COL_PAID_AMOUNT,&paid_amount, B_COL_PAID_DATE,&paid_date, -1);

    gchar *client_esc = g_strescape(client?client:"", NULL);
    gchar *service_esc= g_strescape(service?service:"", NULL);
    gchar *barber_esc = g_strescape(barber?barber:"", NULL);

    g_string_append_printf(out,
      "booking_id=%s|date=%s|time=%s|client=%s|service=%s|barber=%s|amount=%s|paid=%s|paid_amount=%s|paid_date=%s\n",
      nz(booking_id), nz(date), nz(time),
      client_esc?client_esc:"", service_esc?service_esc:"", barber_esc?barber_esc:"",
      nz(amount), nz(paid), nz(paid_amount), nz(paid_date)
    );

    g_free(client_esc); g_free(service_esc); g_free(barber_esc);
    g_free(booking_id); g_free(date); g_free(time); g_free(client); g_free(service); g_free(barber);
    g_free(amount); g_free(paid); g_free(paid_amount); g_free(paid_date);

    valid = gtk_tree_model_iter_next(GTK_TREE_MODEL(app->b_store), &it);
  }

  gboolean ok = g_file_set_contents(BILLING_FILE, out->str, -1, NULL);
  g_string_free(out, TRUE);
  return ok;
}

static void billing_load_file(App *app) {
  if (!app || !app->b_store) return;
  gtk_list_store_clear(app->b_store);

  gchar *contents=NULL; gsize len=0;
  if (!g_file_get_contents(BILLING_FILE, &contents, &len, NULL) || !contents) return;

  gchar **lines = g_strsplit(contents, "\n", -1);
  for (int i=0; lines && lines[i]; i++) {
    if (!lines[i][0]) continue;

    gchar *booking_id=NULL,*date=NULL,*time=NULL,*client=NULL,*service=NULL,*barber=NULL,*amount=NULL,*paid=NULL,*paid_amount=NULL,*paid_date=NULL;
    gchar **parts = g_strsplit(lines[i], "|", -1);
    for (int p=0; parts && parts[p]; p++) {
      if (!booking_id)  booking_id  = b_line_get_val(parts[p], "booking_id");
      if (!date)        date        = b_line_get_val(parts[p], "date");
      if (!time)        time        = b_line_get_val(parts[p], "time");
      if (!client)      client      = b_line_get_val(parts[p], "client");
      if (!service)     service     = b_line_get_val(parts[p], "service");
      if (!barber)      barber      = b_line_get_val(parts[p], "barber");
      if (!amount)      amount      = b_line_get_val(parts[p], "amount");
      if (!paid)        paid        = b_line_get_val(parts[p], "paid");
      if (!paid_amount) paid_amount = b_line_get_val(parts[p], "paid_amount");
      if (!paid_date)   paid_date   = b_line_get_val(parts[p], "paid_date");
    }

    if (client) { gchar *un=g_strcompress(client); g_free(client); client=un?un:g_strdup(""); }
    if (service){ gchar *un=g_strcompress(service);g_free(service);service=un?un:g_strdup("");}
    if (barber) { gchar *un=g_strcompress(barber); g_free(barber); barber=un?un:g_strdup(""); }

    if (!paid) paid = g_strdup("0");
    if (!paid_amount) paid_amount = g_strdup("0.00");
    if (!paid_date) paid_date = g_strdup("");

    billing_store_add(app, booking_id, date, time, client, service, barber, amount, paid, paid_amount, paid_date);

    g_free(booking_id); g_free(date); g_free(time); g_free(client); g_free(service); g_free(barber);
    g_free(amount); g_free(paid); g_free(paid_amount); g_free(paid_date);
    g_strfreev(parts);
  }
  g_strfreev(lines); g_free(contents);

  if (app->b_filter) gtk_tree_model_filter_refilter(app->b_filter);
}

/* mode filter: DIA (date==selected) vs MÊS (YYYY-MM == selected month) */
static gboolean billing_filter_visible(GtkTreeModel *model, GtkTreeIter *iter, gpointer user_data) {
  App *app = (App*)user_data;
  gchar *row_date=NULL;
  gtk_tree_model_get(model, iter, B_COL_DATE, &row_date, -1);
  if (!row_date || !*row_date) { g_free(row_date); return FALSE; }

  gchar *sel = calendar_to_date_str(app->b_cal);

  gboolean month_mode = gtk_toggle_button_get_active(app->b_mode_month);
  gboolean ok = FALSE;

  if (!month_mode) {
    ok = (g_strcmp0(row_date, sel) == 0);
  } else {
    // compare YYYY-MM prefix
    if (strlen(row_date) >= 7 && strlen(sel) >= 7) {
      ok = (strncmp(row_date, sel, 7) == 0);
    }
  }

  g_free(sel);
  g_free(row_date);
  return ok;
}

static void billing_refilter(App *app) {
  if (app && app->b_filter) gtk_tree_model_filter_refilter(app->b_filter);
}

static void billing_update_totals(App *app) {
  if (!app || !app->b_filter) return;

  double total=0.0, received=0.0;

  GtkTreeIter it;
  gboolean valid = gtk_tree_model_get_iter_first(GTK_TREE_MODEL(app->b_filter), &it);
  while (valid) {
    gchar *amount=NULL,*paid_amount=NULL;
    gtk_tree_model_get(GTK_TREE_MODEL(app->b_filter), &it,
                       B_COL_AMOUNT,&amount, B_COL_PAID_AMOUNT,&paid_amount, -1);

    total += amount ? g_ascii_strtod(amount, NULL) : 0.0;
    received += paid_amount ? g_ascii_strtod(paid_amount, NULL) : 0.0;

    g_free(amount); g_free(paid_amount);
    valid = gtk_tree_model_iter_next(GTK_TREE_MODEL(app->b_filter), &it);
  }

  double due = total - received;

  gchar *t1 = g_strdup_printf("Total: %.2f €", total);
  gchar *t2 = g_strdup_printf("Recebido: %.2f €", received);
  gchar *t3 = g_strdup_printf("Em dívida: %.2f €", due);

  gtk_label_set_text(app->b_lbl_total, t1);
  gtk_label_set_text(app->b_lbl_received, t2);
  gtk_label_set_text(app->b_lbl_due, t3);

  g_free(t1); g_free(t2); g_free(t3);
}

static gboolean billing_get_selected_child_iter(App *app, GtkTreeIter *out_child) {
  if (!app || !app->b_list) return FALSE;
  GtkTreeSelection *sel = gtk_tree_view_get_selection(app->b_list);
  GtkTreeModel *model=NULL; GtkTreeIter it;
  if (!gtk_tree_selection_get_selected(sel,&model,&it)) return FALSE;

  if (GTK_IS_TREE_MODEL_FILTER(model) && app->b_filter) {
    GtkTreeIter child;
    gtk_tree_model_filter_convert_iter_to_child_iter(app->b_filter, &child, &it);
    if (out_child) *out_child = child;
    return TRUE;
  }
  if (out_child) *out_child = it;
  return TRUE;
}

static double billing_balance_for_client(App *app, const gchar *client_name) {
  if (!app || !app->b_store || !client_name || !*client_name) return 0.0;

  double total=0.0, paid=0.0;
  GtkTreeIter it;
  gboolean valid = gtk_tree_model_get_iter_first(GTK_TREE_MODEL(app->b_store), &it);
  while (valid) {
    gchar *client=NULL,*amount=NULL,*paid_amount=NULL;
    gtk_tree_model_get(GTK_TREE_MODEL(app->b_store), &it,
                       B_COL_CLIENT,&client, B_COL_AMOUNT,&amount, B_COL_PAID_AMOUNT,&paid_amount, -1);

    if (client && g_strcmp0(client, client_name)==0) {
      total += amount ? g_ascii_strtod(amount, NULL) : 0.0;
      paid  += paid_amount ? g_ascii_strtod(paid_amount, NULL) : 0.0;
    }

    g_free(client); g_free(amount); g_free(paid_amount);
    valid = gtk_tree_model_iter_next(GTK_TREE_MODEL(app->b_store), &it);
  }
  return total - paid;
}

static void billing_update_selected_panel(App *app) {
  GtkTreeIter it;
  if (!billing_get_selected_child_iter(app, &it)) {
    gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(app->b_chk_paid), FALSE);
    gtk_entry_set_text(app->b_paid_amount, "");
    gtk_label_set_text(app->b_lbl_client_balance, "Saldo do cliente: —");
    return;
  }

  gchar *client=NULL,*amount=NULL,*paid=NULL,*paid_amount=NULL;
  gtk_tree_model_get(GTK_TREE_MODEL(app->b_store), &it,
                     B_COL_CLIENT,&client, B_COL_AMOUNT,&amount,
                     B_COL_PAID,&paid, B_COL_PAID_AMOUNT,&paid_amount, -1);

  gboolean is_paid = (paid && g_strcmp0(paid,"1")==0);
  gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(app->b_chk_paid), is_paid);

  // mostra paid_amount atual
  gtk_entry_set_text(app->b_paid_amount, paid_amount?paid_amount:"");

  double bal = billing_balance_for_client(app, client?client:"");
  gchar *lbl = g_strdup_printf("Saldo do cliente: %.2f €", bal);
  gtk_label_set_text(app->b_lbl_client_balance, lbl);
  g_free(lbl);

  g_free(client); g_free(amount); g_free(paid); g_free(paid_amount);
}

static void on_billing_selection_changed(GtkTreeSelection *sel, gpointer user_data) {
  (void)sel;
  App *app = (App*)user_data;
  billing_update_selected_panel(app);
}

static void billing_apply_paid_state(App *app, gboolean force_paid, gboolean force_unpaid) {
  GtkTreeIter it;
  if (!billing_get_selected_child_iter(app, &it)) {
    msg(app->win, GTK_MESSAGE_INFO, "Faturação", "Seleciona uma linha.");
    return;
  }

  gchar *amount=NULL,*client=NULL;
  gtk_tree_model_get(GTK_TREE_MODEL(app->b_store), &it, B_COL_AMOUNT,&amount, B_COL_CLIENT,&client, -1);

  gboolean paid = gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(app->b_chk_paid));
  if (force_paid) paid = TRUE;
  if (force_unpaid) paid = FALSE;

  const char *entered = gtk_entry_get_text(app->b_paid_amount);
  double a = amount ? g_ascii_strtod(amount, NULL) : 0.0;
  double pa = 0.0;

  if (paid) {
    if (entered && *entered) pa = g_ascii_strtod(entered, NULL);
    else pa = a;
    if (pa < 0) pa = 0;
    if (pa > a) pa = a;
  } else {
    pa = 0.0;
  }

  gchar *paid_s = g_strdup(paid ? "1" : "0");
  gchar *paid_amount_s = g_strdup_printf("%.2f", pa);

  gchar *paid_date_s = NULL;
  if (paid) {
    paid_date_s = calendar_to_date_str(app->b_cal);
  } else {
    paid_date_s = g_strdup("");
  }

  gtk_list_store_set(app->b_store, &it,
                     B_COL_PAID, paid_s,
                     B_COL_PAID_AMOUNT, paid_amount_s,
                     B_COL_PAID_DATE, paid_date_s,
                     -1);

  billing_save_file(app);
  billing_refilter(app);
  billing_update_totals(app);

  // atualizar saldo label
  double bal = billing_balance_for_client(app, client?client:"");
  gchar *lbl = g_strdup_printf("Saldo do cliente: %.2f €", bal);
  gtk_label_set_text(app->b_lbl_client_balance, lbl);
  g_free(lbl);

  g_free(amount); g_free(client);
  g_free(paid_s); g_free(paid_amount_s); g_free(paid_date_s);
}

static void on_billing_apply_clicked(GtkButton *b, gpointer user_data) { (void)b; billing_apply_paid_state((App*)user_data, FALSE, FALSE); }
static void on_billing_set_paid(GtkButton *b, gpointer user_data) { (void)b; billing_apply_paid_state((App*)user_data, TRUE, FALSE); }
static void on_billing_set_unpaid(GtkButton *b, gpointer user_data) { (void)b; billing_apply_paid_state((App*)user_data, FALSE, TRUE); }

static void on_billing_calendar_changed(GtkCalendar *cal, gpointer user_data) {
  (void)cal;
  App *app = (App*)user_data;
  billing_refilter(app);
  billing_update_totals(app);
}

static void on_billing_mode_toggled(GtkToggleButton *tb, gpointer user_data) {
  (void)tb;
  App *app = (App*)user_data;
  billing_refilter(app);
  billing_update_totals(app);
}

/* cria linhas de faturação a partir do estado "Concluido" no ficheiro do dia.
   Regras simples:
   - só cria se não existir booking_id no faturacao.txt
   - amount por serviço: Corte=15, Barba=10, Corte+Barba=22 (podes mudar aqui)
*/
static double price_for_service(const char *svc) {
  if (!svc) return 0.0;
  if (g_strcmp0(svc, "Corte")==0) return 13.0;
  if (g_strcmp0(svc, "Barba")==0) return 2.0;
  if (g_strcmp0(svc, "Corte+Barba")==0) return 15.0;
  return 0.0;
}

static gboolean billing_has_booking_id(App *app, const gchar *bid) {
  if (!app || !app->b_store || !bid || !*bid) return FALSE;
  GtkTreeIter it;
  gboolean valid = gtk_tree_model_get_iter_first(GTK_TREE_MODEL(app->b_store), &it);
  while (valid) {
    gchar *x=NULL;
    gtk_tree_model_get(GTK_TREE_MODEL(app->b_store), &it, B_COL_BOOKING_ID, &x, -1);
    gboolean ok = (x && g_strcmp0(x, bid)==0);
    g_free(x);
    if (ok) return TRUE;
    valid = gtk_tree_model_iter_next(GTK_TREE_MODEL(app->b_store), &it);
  }
  return FALSE;
}

static void billing_sync_from_agenda_selected_day(App *app) {
  if (!app) return;

  gchar *day = calendar_to_date_str(app->a_cal);
  gchar *file = agenda_file_for_calendar(app->a_cal);

  gchar *contents=NULL; gsize len=0;
  if (!g_file_get_contents(file, &contents, &len, NULL) || !contents) {
    msg(app->win, GTK_MESSAGE_INFO, "Faturação", "Sem ficheiro de agenda para este dia.");
    g_free(day); g_free(file);
    return;
  }

  int added = 0;

  gchar **lines = g_strsplit(contents, "\n", -1);
  for (int i=0; lines && lines[i]; i++) {
    if (!lines[i][0]) continue;

    gchar *id=NULL,*time=NULL,*client=NULL,*service=NULL,*barber=NULL,*status=NULL;

    gchar **parts = g_strsplit(lines[i], "|", -1);
    for (int p=0; parts && parts[p]; p++) {
      if (!id)      id      = line_get_val(parts[p], "id");
      if (!time)    time    = line_get_val(parts[p], "time");
      if (!client)  client  = line_get_val(parts[p], "client");
      if (!service) service = line_get_val(parts[p], "service");
      if (!barber)  barber  = line_get_val(parts[p], "barber");
      if (!status)  status  = line_get_val(parts[p], "status");
    }

    if (status && g_strcmp0(status, "Concluido")==0 && id && *id) {
      if (!billing_has_booking_id(app, id)) {
        double pr = price_for_service(service);
        gchar *amount = g_strdup_printf("%.2f", pr);

        // default: não pago
        billing_store_add(app, id, day, nz(time), nz(client), nz(service), nz(barber),
                          amount, "0", "0.00", "");

        g_free(amount);
        added++;
      }
    }

    g_free(id); g_free(time); g_free(client); g_free(service); g_free(barber); g_free(status);
    g_strfreev(parts);
  }

  g_strfreev(lines);
  g_free(contents);
  g_free(file);
  g_free(day);

  if (added > 0) {
    billing_save_file(app);
    billing_refilter(app);
    billing_update_totals(app);
    msg(app->win, GTK_MESSAGE_INFO, "Faturação", "Sincronizado: %d itens concluídos adicionados.", added);
  } else {
    msg(app->win, GTK_MESSAGE_INFO, "Faturação", "Nada novo para sincronizar (ou ainda não está Concluído).");
  }
}

/* ----------------- Build UI ----------------- */
static GtkWidget* make_logo_header(App *app) {
  GtkWidget *bar = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 14);
  gtk_container_set_border_width(GTK_CONTAINER(bar), 10);

  app->img_logo = GTK_IMAGE(gtk_image_new());

  gchar *logo_path = g_build_filename(APP_DATA_DIR, "logo.png", NULL);
  if (!g_file_test(logo_path, G_FILE_TEST_EXISTS)) { g_free(logo_path); logo_path = g_build_filename(APP_DATA_DIR, "logo.jpg", NULL); }
  if (!g_file_test(logo_path, G_FILE_TEST_EXISTS)) { g_free(logo_path); logo_path = g_build_filename(APP_DATA_DIR, "logo.jpeg", NULL); }

  if (g_file_test(logo_path, G_FILE_TEST_EXISTS)) {
    GError *err=NULL;
    GdkPixbuf *pb = gdk_pixbuf_new_from_file_at_scale(logo_path, 72,72, TRUE, &err);
    if (pb) { gtk_image_set_from_pixbuf(app->img_logo,pb); g_object_unref(pb); }
    else { gtk_image_set_from_icon_name(app->img_logo,"emblem-favorite",GTK_ICON_SIZE_DIALOG); if (err) g_error_free(err); }
  } else {
    gtk_image_set_from_icon_name(app->img_logo,"emblem-favorite",GTK_ICON_SIZE_DIALOG);
  }
  g_free(logo_path);

  app->lbl_title = GTK_LABEL(gtk_label_new(NULL));
  gtk_label_set_markup(app->lbl_title, "<span size='xx-large' weight='bold'>Barbearia Neves</span>");
  gtk_label_set_xalign(app->lbl_title, 0.0);

  GtkWidget *subtitle = gtk_label_new("");
  gtk_label_set_xalign(GTK_LABEL(subtitle), 0.0);

  GtkWidget *text = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
  gtk_box_pack_start(GTK_BOX(text), GTK_WIDGET(app->lbl_title), FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(text), subtitle, FALSE, FALSE, 0);

  gtk_box_pack_start(GTK_BOX(bar), GTK_WIDGET(app->img_logo), FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(bar), text, TRUE, TRUE, 0);

  GtkWidget *frame = gtk_frame_new(NULL);
  gtk_container_add(GTK_CONTAINER(frame), bar);
  return frame;
}

/* ---- Clients tab ---- */
static GtkWidget* make_clients_list(App *app) {
  app->c_store = gtk_list_store_new(C_NCOLS, G_TYPE_STRING, G_TYPE_STRING, G_TYPE_STRING, G_TYPE_STRING);
  app->c_list = GTK_TREE_VIEW(gtk_tree_view_new_with_model(GTK_TREE_MODEL(app->c_store)));
  gtk_tree_view_set_headers_visible(app->c_list, TRUE);

  struct { const char *t; int col; int minw; } cols[] = {
    {"Nome", C_COL_NAME, 180},
    {"Telefone", C_COL_PHONE, 120},
    {"Email", C_COL_EMAIL, 180},
  };

  for (guint i=0;i<sizeof(cols)/sizeof(cols[0]);i++){
    GtkCellRenderer *r = gtk_cell_renderer_text_new();
    GtkTreeViewColumn *c = gtk_tree_view_column_new_with_attributes(cols[i].t, r, "text", cols[i].col, NULL);
    gtk_tree_view_column_set_resizable(c, TRUE);
    gtk_tree_view_column_set_min_width(c, cols[i].minw);
    gtk_tree_view_append_column(app->c_list, c);
  }

  GtkTreeSelection *sel = gtk_tree_view_get_selection(app->c_list);
  g_signal_connect(sel, "changed", G_CALLBACK(on_client_list_selection), app);

  GtkWidget *sw = gtk_scrolled_window_new(NULL,NULL);
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(sw), GTK_POLICY_AUTOMATIC, GTK_POLICY_AUTOMATIC);
  gtk_container_add(GTK_CONTAINER(sw), GTK_WIDGET(app->c_list));
  return sw;
}

static GtkWidget* make_client_photo_panel(App *app, const gchar *title, GtkImage **out_img, gboolean is_before) {
  GtkWidget *frame = gtk_frame_new(title);
  GtkWidget *v = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
  gtk_container_set_border_width(GTK_CONTAINER(v), 8);

  GtkWidget *img = gtk_image_new_from_icon_name("image-missing", GTK_ICON_SIZE_DIALOG);
  gtk_widget_set_hexpand(img, TRUE);
  gtk_widget_set_vexpand(img, TRUE);

  GtkWidget *btns = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
  GtkWidget *b_load = gtk_button_new_with_label("Carregar…");
  GtkWidget *b_view = gtk_button_new_with_label("Ver");
  GtkWidget *b_rm   = gtk_button_new_with_label("Remover");
  gtk_box_pack_start(GTK_BOX(btns), b_load, TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(btns), b_view, TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(btns), b_rm,   TRUE, TRUE, 0);

  static PhotoCtx ctx_before, ctx_after;
  PhotoCtx *ctx = is_before ? &ctx_before : &ctx_after;
  ctx->app = app; ctx->is_before = is_before;

  g_signal_connect(b_load,"clicked",G_CALLBACK(on_client_photo_load), ctx);
  g_signal_connect(b_view,"clicked",G_CALLBACK(on_client_photo_view), ctx);
  g_signal_connect(b_rm,  "clicked",G_CALLBACK(on_client_photo_remove), ctx);

  gtk_box_pack_start(GTK_BOX(v), img, TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(v), btns, FALSE, FALSE, 0);
  gtk_container_add(GTK_CONTAINER(frame), v);

  *out_img = GTK_IMAGE(img);
  return frame;
}

static GtkWidget* make_clients_tab(App *app) {
  GtkWidget *root = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 10);
  gtk_container_set_border_width(GTK_CONTAINER(root), 10);

  GtkWidget *left = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
  gtk_widget_set_size_request(left, 420, -1);

  app->c_search = GTK_ENTRY(gtk_entry_new());
  gtk_entry_set_placeholder_text(app->c_search, "Pesquisar (nome/telefone/email)...");
  g_signal_connect(app->c_search, "changed", G_CALLBACK(on_client_search_changed), app);

  GtkWidget *list = make_clients_list(app);

  GtkWidget *left_actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
  GtkWidget *b_new = gtk_button_new_with_label("Novo");
  GtkWidget *b_save= gtk_button_new_with_label("Guardar");
  GtkWidget *b_del = gtk_button_new_with_label("Apagar");
  gtk_box_pack_start(GTK_BOX(left_actions), b_new, TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(left_actions), b_save, TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(left_actions), b_del, TRUE, TRUE, 0);

  g_signal_connect(b_new, "clicked", G_CALLBACK(on_client_new), app);
  g_signal_connect(b_save,"clicked", G_CALLBACK(on_client_save), app);
  g_signal_connect(b_del, "clicked", G_CALLBACK(on_client_delete), app);

  gtk_box_pack_start(GTK_BOX(left), labeled(GTK_WIDGET(app->c_search), "Clientes"), FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(left), list, TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(left), left_actions, FALSE, FALSE, 0);

  GtkWidget *right_content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 10);

  GtkWidget *grid = gtk_grid_new();
  gtk_grid_set_row_spacing(GTK_GRID(grid), 8);
  gtk_grid_set_column_spacing(GTK_GRID(grid), 10);

  app->c_name = GTK_ENTRY(gtk_entry_new());
  app->c_phone = GTK_ENTRY(gtk_entry_new());
  app->c_email = GTK_ENTRY(gtk_entry_new());
  app->c_profession = GTK_ENTRY(gtk_entry_new());
  app->c_age = GTK_ENTRY(gtk_entry_new());

  gtk_entry_set_placeholder_text(app->c_name,"Nome do cliente");
  gtk_entry_set_placeholder_text(app->c_phone,"Telefone");
  gtk_entry_set_placeholder_text(app->c_email,"email@exemplo.com");
  gtk_entry_set_placeholder_text(app->c_profession,"Profissão");
  gtk_entry_set_placeholder_text(app->c_age,"Idade (ex: 28)");

  GtkWidget *sw_notes = gtk_scrolled_window_new(NULL,NULL);
  gtk_widget_set_size_request(sw_notes, -1, 100);
  app->c_notes = GTK_TEXT_VIEW(gtk_text_view_new());
  gtk_text_view_set_wrap_mode(app->c_notes, GTK_WRAP_WORD_CHAR);
  gtk_container_add(GTK_CONTAINER(sw_notes), GTK_WIDGET(app->c_notes));

  gtk_grid_attach(GTK_GRID(grid), labeled(GTK_WIDGET(app->c_name), "Nome"), 0,0,2,1);
  gtk_grid_attach(GTK_GRID(grid), labeled(GTK_WIDGET(app->c_phone),"Telefone"), 0,1,1,1);
  gtk_grid_attach(GTK_GRID(grid), labeled(GTK_WIDGET(app->c_email),"Email"), 1,1,1,1);
  gtk_grid_attach(GTK_GRID(grid), labeled(GTK_WIDGET(app->c_profession),"Profissão"), 0,2,1,1);
  gtk_grid_attach(GTK_GRID(grid), labeled(GTK_WIDGET(app->c_age),"Idade"), 1,2,1,1);
  gtk_grid_attach(GTK_GRID(grid), labeled(sw_notes,"Notas"), 0,3,2,1);

  GtkWidget *form_frame = gtk_frame_new("Ficha do cliente");
  GtkWidget *form_pad = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
  gtk_container_set_border_width(GTK_CONTAINER(form_pad), 8);
  gtk_box_pack_start(GTK_BOX(form_pad), grid, FALSE, FALSE, 0);
  gtk_container_add(GTK_CONTAINER(form_frame), form_pad);

  GtkWidget *photos = gtk_grid_new();
  gtk_grid_set_column_spacing(GTK_GRID(photos), 10);
  gtk_widget_set_hexpand(photos, TRUE);
  gtk_widget_set_vexpand(photos, TRUE);

  GtkWidget *p_before = make_client_photo_panel(app,"Foto Antes",&app->c_img_before,TRUE);
  GtkWidget *p_after  = make_client_photo_panel(app,"Foto Depois",&app->c_img_after,FALSE);

  gtk_grid_attach(GTK_GRID(photos), p_before, 0,0,1,1);
  gtk_grid_attach(GTK_GRID(photos), p_after,  1,0,1,1);

  GtkWidget *photos_frame = gtk_frame_new("Antes / Depois");
  GtkWidget *photos_pad = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  gtk_container_set_border_width(GTK_CONTAINER(photos_pad), 8);
  gtk_box_pack_start(GTK_BOX(photos_pad), photos, TRUE, TRUE, 0);
  gtk_container_add(GTK_CONTAINER(photos_frame), photos_pad);

  gtk_box_pack_start(GTK_BOX(right_content), form_frame, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(right_content), photos_frame, TRUE, TRUE, 0);

  GtkWidget *right_sw = gtk_scrolled_window_new(NULL,NULL);
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(right_sw), GTK_POLICY_AUTOMATIC, GTK_POLICY_AUTOMATIC);
  gtk_container_add(GTK_CONTAINER(right_sw), right_content);

  gtk_box_pack_start(GTK_BOX(root), left, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(root), right_sw, TRUE, TRUE, 0);

  return root;
}

/* ---- Agenda tab ---- */
static GtkWidget* make_agenda_list(App *app) {
  app->a_store = gtk_list_store_new(A_NCOLS,
    G_TYPE_STRING,G_TYPE_STRING,G_TYPE_STRING,G_TYPE_STRING,G_TYPE_STRING,
    G_TYPE_STRING,G_TYPE_STRING,G_TYPE_STRING,G_TYPE_STRING
  );

  app->a_filter = GTK_TREE_MODEL_FILTER(gtk_tree_model_filter_new(GTK_TREE_MODEL(app->a_store), NULL));
  gtk_tree_model_filter_set_visible_func(app->a_filter, agenda_filter_visible, app, NULL);

  app->a_list = GTK_TREE_VIEW(gtk_tree_view_new_with_model(GTK_TREE_MODEL(app->a_filter)));
  gtk_tree_view_set_headers_visible(app->a_list, TRUE);

  struct { const char *t; int col; int minw; } cols[] = {
    {"Hora", A_COL_TIME, 70},
    {"Cliente", A_COL_CLIENT, 220},
    {"Serviço", A_COL_SERVICE, 140},
    {"Barbeiro", A_COL_BARBER, 120},
    {"Estado", A_COL_STATUS, 110},
  };

  for (guint i=0;i<sizeof(cols)/sizeof(cols[0]);i++){
    GtkCellRenderer *r = gtk_cell_renderer_text_new();
    GtkTreeViewColumn *c = gtk_tree_view_column_new_with_attributes(cols[i].t, r, "text", cols[i].col, NULL);
    gtk_tree_view_column_set_resizable(c, TRUE);
    gtk_tree_view_column_set_min_width(c, cols[i].minw);
    gtk_tree_view_append_column(app->a_list, c);
  }

  GtkTreeSelection *sel = gtk_tree_view_get_selection(app->a_list);
  g_signal_connect(sel, "changed", G_CALLBACK(on_agenda_selection_changed), app);
  g_signal_connect(app->a_list, "row-activated", G_CALLBACK(on_agenda_row_activated), app);

  GtkWidget *sw = gtk_scrolled_window_new(NULL,NULL);
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(sw), GTK_POLICY_AUTOMATIC, GTK_POLICY_AUTOMATIC);
  gtk_container_add(GTK_CONTAINER(sw), GTK_WIDGET(app->a_list));
  return sw;
}

static GtkWidget* make_agenda_filters_bar(App *app) {
  GtkWidget *bar = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 10);

  app->a_f_barber = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  gtk_combo_box_text_append_text(app->a_f_barber, "Todos");
  gtk_combo_box_text_append_text(app->a_f_barber, "Pedro");
  gtk_combo_box_text_append_text(app->a_f_barber, "Joao");
  gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_f_barber), 0);

  app->a_f_status = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  gtk_combo_box_text_append_text(app->a_f_status, "Todos");
  gtk_combo_box_text_append_text(app->a_f_status, "Marcado");
  gtk_combo_box_text_append_text(app->a_f_status, "Chegou");
  gtk_combo_box_text_append_text(app->a_f_status, "EmAtendimento");
  gtk_combo_box_text_append_text(app->a_f_status, "Concluido");
  gtk_combo_box_text_append_text(app->a_f_status, "Cancelado");
  gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_f_status), 0);

  g_signal_connect(app->a_f_barber,"changed",G_CALLBACK(on_filter_barber_changed), app);
  g_signal_connect(app->a_f_status,"changed",G_CALLBACK(on_filter_status_changed), app);

  GtkWidget *b_email = gtk_button_new_with_label("📧 Email");
  GtkWidget *b_pdf   = gtk_button_new_with_label("🖨 Exportar PDF");
  g_signal_connect(b_email, "clicked", G_CALLBACK(on_agenda_email_selected), app);
  g_signal_connect(b_pdf,   "clicked", G_CALLBACK(on_agenda_export_pdf), app);

  gtk_box_pack_start(GTK_BOX(bar), labeled(GTK_WIDGET(app->a_f_barber), "Filtrar Barbeiro"), FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(bar), labeled(GTK_WIDGET(app->a_f_status), "Filtrar Estado"), FALSE, FALSE, 0);
  gtk_box_pack_end(GTK_BOX(bar), b_pdf, FALSE, FALSE, 0);
  gtk_box_pack_end(GTK_BOX(bar), b_email, FALSE, FALSE, 0);

  GtkWidget *frame = gtk_frame_new(NULL);
  gtk_container_add(GTK_CONTAINER(frame), bar);
  return frame;
}

static GtkWidget* make_agenda_form(App *app) {
  GtkWidget *grid = gtk_grid_new();
  gtk_grid_set_row_spacing(GTK_GRID(grid), 8);
  gtk_grid_set_column_spacing(GTK_GRID(grid), 10);

  app->a_time = GTK_ENTRY(gtk_entry_new());
  gtk_entry_set_placeholder_text(app->a_time, "HH:MM (ex: 14:30)");

  app->a_dur = GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(10, 240, 5));
  gtk_spin_button_set_value(app->a_dur, 30);

  app->a_client = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  gtk_combo_box_text_append(app->a_client, "", "— escolher —");
  gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_client), 0);

  app->a_service = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  gtk_combo_box_text_append_text(app->a_service, "Corte");
  gtk_combo_box_text_append_text(app->a_service, "Barba");
  gtk_combo_box_text_append_text(app->a_service, "Corte+Barba");
  gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_service), 0);

  app->a_barber = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  gtk_combo_box_text_append_text(app->a_barber, "Joao");
  gtk_combo_box_text_append_text(app->a_barber, "Pedro");
  gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_barber), 0);

  app->a_status = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  gtk_combo_box_text_append_text(app->a_status, "Marcado");
  gtk_combo_box_text_append_text(app->a_status, "Chegou");
  gtk_combo_box_text_append_text(app->a_status, "EmAtendimento");
  gtk_combo_box_text_append_text(app->a_status, "Concluido");
  gtk_combo_box_text_append_text(app->a_status, "Cancelado");
  gtk_combo_box_set_active(GTK_COMBO_BOX(app->a_status), 0);

  app->a_notes = GTK_TEXT_VIEW(gtk_text_view_new());
  gtk_text_view_set_wrap_mode(app->a_notes, GTK_WRAP_WORD_CHAR);
  GtkWidget *sw_notes = gtk_scrolled_window_new(NULL,NULL);
  gtk_widget_set_size_request(sw_notes, -1, 80);
  gtk_container_add(GTK_CONTAINER(sw_notes), GTK_WIDGET(app->a_notes));

  gtk_grid_attach(GTK_GRID(grid), labeled(GTK_WIDGET(app->a_time), "Hora"), 0,0,1,1);
  gtk_grid_attach(GTK_GRID(grid), labeled(GTK_WIDGET(app->a_dur), "Duração (min)"), 1,0,1,1);
  gtk_grid_attach(GTK_GRID(grid), labeled(GTK_WIDGET(app->a_client), "Cliente"), 0,1,2,1);
  gtk_grid_attach(GTK_GRID(grid), labeled(GTK_WIDGET(app->a_service), "Serviço"), 0,2,1,1);
  gtk_grid_attach(GTK_GRID(grid), labeled(GTK_WIDGET(app->a_barber), "Barbeiro"), 1,2,1,1);
  gtk_grid_attach(GTK_GRID(grid), labeled(GTK_WIDGET(app->a_status), "Estado"), 0,3,2,1);
  gtk_grid_attach(GTK_GRID(grid), labeled(sw_notes, "Notas"), 0,4,2,1);

  GtkWidget *actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
  GtkWidget *b_new = gtk_button_new_with_label("Novo");
  GtkWidget *b_add = gtk_button_new_with_label("Adicionar");
  GtkWidget *b_upd = gtk_button_new_with_label("Atualizar");
  GtkWidget *b_del = gtk_button_new_with_label("Apagar");
  GtkWidget *b_save= gtk_button_new_with_label("Guardar Dia");
  gtk_box_pack_start(GTK_BOX(actions), b_new, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(actions), b_add, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(actions), b_upd, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(actions), b_del, FALSE, FALSE, 0);
  gtk_box_pack_end(GTK_BOX(actions), b_save, FALSE, FALSE, 0);

  g_signal_connect(b_new, "clicked", G_CALLBACK(on_agenda_new), app);
  g_signal_connect(b_add, "clicked", G_CALLBACK(on_agenda_add), app);
  g_signal_connect(b_upd, "clicked", G_CALLBACK(on_agenda_update), app);
  g_signal_connect(b_del, "clicked", G_CALLBACK(on_agenda_delete), app);
  g_signal_connect(b_save,"clicked", G_CALLBACK(on_agenda_save_day), app);

  GtkWidget *wrap = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
  gtk_container_set_border_width(GTK_CONTAINER(wrap), 8);
  gtk_box_pack_start(GTK_BOX(wrap), grid, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(wrap), actions, FALSE, FALSE, 0);

  GtkWidget *frame = gtk_frame_new("Marcação");
  gtk_container_add(GTK_CONTAINER(frame), wrap);
  return frame;
}

static GtkWidget* make_agenda_tab(App *app) {
  GtkWidget *root = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 10);
  gtk_container_set_border_width(GTK_CONTAINER(root), 10);

  app->a_cal = GTK_CALENDAR(gtk_calendar_new());
  g_signal_connect(app->a_cal, "day-selected",   G_CALLBACK(on_agenda_calendar_changed), app);
  g_signal_connect(app->a_cal, "month-changed",  G_CALLBACK(on_agenda_calendar_changed), app);

  GtkWidget *left = gtk_frame_new("Calendário");
  GtkWidget *left_pad = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
  gtk_container_set_border_width(GTK_CONTAINER(left_pad), 8);
  gtk_box_pack_start(GTK_BOX(left_pad), GTK_WIDGET(app->a_cal), FALSE, FALSE, 0);
  gtk_container_add(GTK_CONTAINER(left), left_pad);

  GtkWidget *filters = make_agenda_filters_bar(app);

  GtkWidget *list_frame = gtk_frame_new("Marcações do dia");
  gtk_container_add(GTK_CONTAINER(list_frame), make_agenda_list(app));

  GtkWidget *form = make_agenda_form(app);

  GtkWidget *right_content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
  gtk_box_pack_start(GTK_BOX(right_content), filters, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(right_content), list_frame, TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(right_content), form, FALSE, FALSE, 0);

  GtkWidget *right_sw = gtk_scrolled_window_new(NULL,NULL);
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(right_sw), GTK_POLICY_AUTOMATIC, GTK_POLICY_AUTOMATIC);
  gtk_container_add(GTK_CONTAINER(right_sw), right_content);

  gtk_box_pack_start(GTK_BOX(root), left, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(root), right_sw, TRUE, TRUE, 0);

  return root;
}

/* ---- Billing tab (OPÇÃO 1) ---- */
static GtkWidget* make_billing_list(App *app) {
  app->b_store = gtk_list_store_new(B_NCOLS,
    G_TYPE_STRING,G_TYPE_STRING,G_TYPE_STRING,G_TYPE_STRING,G_TYPE_STRING,
    G_TYPE_STRING,G_TYPE_STRING,G_TYPE_STRING,G_TYPE_STRING,G_TYPE_STRING
  );

  app->b_filter = GTK_TREE_MODEL_FILTER(gtk_tree_model_filter_new(GTK_TREE_MODEL(app->b_store), NULL));
  gtk_tree_model_filter_set_visible_func(app->b_filter, billing_filter_visible, app, NULL);

  app->b_list = GTK_TREE_VIEW(gtk_tree_view_new_with_model(GTK_TREE_MODEL(app->b_filter)));
  gtk_tree_view_set_headers_visible(app->b_list, TRUE);

  struct { const char *t; int col; int minw; } cols[] = {
    {"Data",   B_COL_DATE,   90},
    {"Hora",   B_COL_TIME,   60},
    {"Cliente",B_COL_CLIENT, 200},
    {"Serviço",B_COL_SERVICE,120},
    {"Barbeiro",B_COL_BARBER,110},
    {"Valor",  B_COL_AMOUNT, 70},
    {"Pago",   B_COL_PAID,   50},
    {"Pago €", B_COL_PAID_AMOUNT, 70},
  };

  for (guint i=0;i<sizeof(cols)/sizeof(cols[0]);i++){
    GtkCellRenderer *r = gtk_cell_renderer_text_new();
    GtkTreeViewColumn *c = gtk_tree_view_column_new_with_attributes(cols[i].t, r, "text", cols[i].col, NULL);
    gtk_tree_view_column_set_resizable(c, TRUE);
    gtk_tree_view_column_set_min_width(c, cols[i].minw);
    gtk_tree_view_append_column(app->b_list, c);
  }

  GtkTreeSelection *sel = gtk_tree_view_get_selection(app->b_list);
  g_signal_connect(sel, "changed", G_CALLBACK(on_billing_selection_changed), app);

  GtkWidget *sw = gtk_scrolled_window_new(NULL,NULL);
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(sw), GTK_POLICY_AUTOMATIC, GTK_POLICY_AUTOMATIC);
  gtk_container_add(GTK_CONTAINER(sw), GTK_WIDGET(app->b_list));
  return sw;
}

static GtkWidget* make_billing_tab(App *app) {
  GtkWidget *root = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 10);
  gtk_container_set_border_width(GTK_CONTAINER(root), 10);

  // LEFT: calendário + modo + sync
  GtkWidget *left = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
  gtk_widget_set_size_request(left, 320, -1);

  app->b_cal = GTK_CALENDAR(gtk_calendar_new());
  g_signal_connect(app->b_cal, "day-selected",  G_CALLBACK(on_billing_calendar_changed), app);
  g_signal_connect(app->b_cal, "month-changed", G_CALLBACK(on_billing_calendar_changed), app);

  GtkWidget *mode_box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
  app->b_mode_day = GTK_TOGGLE_BUTTON(gtk_radio_button_new_with_label(NULL, "DIA"));
  app->b_mode_month = GTK_TOGGLE_BUTTON(gtk_radio_button_new_with_label_from_widget(GTK_RADIO_BUTTON(app->b_mode_day), "MÊS"));
  gtk_toggle_button_set_active(app->b_mode_day, TRUE);

  g_signal_connect(app->b_mode_day, "toggled", G_CALLBACK(on_billing_mode_toggled), app);
  g_signal_connect(app->b_mode_month, "toggled", G_CALLBACK(on_billing_mode_toggled), app);

  gtk_box_pack_start(GTK_BOX(mode_box), GTK_WIDGET(app->b_mode_day), TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(mode_box), GTK_WIDGET(app->b_mode_month), TRUE, TRUE, 0);

  GtkWidget *b_sync = gtk_button_new_with_label("↺ Sincronizar Concluídos (dia)");
  g_signal_connect_swapped(b_sync, "clicked", G_CALLBACK(billing_sync_from_agenda_selected_day), app);

  GtkWidget *totals = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
  app->b_lbl_total = GTK_LABEL(gtk_label_new("Total: 0.00 €"));
  app->b_lbl_received = GTK_LABEL(gtk_label_new("Recebido: 0.00 €"));
  app->b_lbl_due = GTK_LABEL(gtk_label_new("Em dívida: 0.00 €"));
  gtk_label_set_xalign(app->b_lbl_total, 0.0);
  gtk_label_set_xalign(app->b_lbl_received, 0.0);
  gtk_label_set_xalign(app->b_lbl_due, 0.0);
  gtk_box_pack_start(GTK_BOX(totals), GTK_WIDGET(app->b_lbl_total), FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(totals), GTK_WIDGET(app->b_lbl_received), FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(totals), GTK_WIDGET(app->b_lbl_due), FALSE, FALSE, 0);

  GtkWidget *left_frame = gtk_frame_new("Data / Modo");
  GtkWidget *left_pad = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
  gtk_container_set_border_width(GTK_CONTAINER(left_pad), 8);
  gtk_box_pack_start(GTK_BOX(left_pad), GTK_WIDGET(app->b_cal), FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(left_pad), mode_box, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(left_pad), b_sync, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(left_pad), totals, FALSE, FALSE, 0);
  gtk_container_add(GTK_CONTAINER(left_frame), left_pad);

  gtk_box_pack_start(GTK_BOX(left), left_frame, FALSE, FALSE, 0);

  // RIGHT: lista + painel de pagamento
  GtkWidget *right = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);

  GtkWidget *list_frame = gtk_frame_new("Faturação");
  gtk_container_add(GTK_CONTAINER(list_frame), make_billing_list(app));

  GtkWidget *pay_frame = gtk_frame_new("Pagamento / Saldo");
  GtkWidget *pay = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
  gtk_container_set_border_width(GTK_CONTAINER(pay), 8);

  app->b_chk_paid = GTK_CHECK_BUTTON(gtk_check_button_new_with_label("Pago"));
  app->b_paid_amount = GTK_ENTRY(gtk_entry_new());
  gtk_entry_set_placeholder_text(app->b_paid_amount, "Valor pago (vazio = total)");

  GtkWidget *btns = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
  app->b_btn_apply_paid = GTK_BUTTON(gtk_button_new_with_label("Aplicar"));
  app->b_btn_set_paid   = GTK_BUTTON(gtk_button_new_with_label("Marcar Pago"));
  app->b_btn_set_unpaid = GTK_BUTTON(gtk_button_new_with_label("Marcar Não Pago"));

  g_signal_connect(app->b_btn_apply_paid, "clicked", G_CALLBACK(on_billing_apply_clicked), app);
  g_signal_connect(app->b_btn_set_paid,   "clicked", G_CALLBACK(on_billing_set_paid), app);
  g_signal_connect(app->b_btn_set_unpaid, "clicked", G_CALLBACK(on_billing_set_unpaid), app);

  gtk_box_pack_start(GTK_BOX(btns), GTK_WIDGET(app->b_btn_set_paid), TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(btns), GTK_WIDGET(app->b_btn_set_unpaid), TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(btns), GTK_WIDGET(app->b_btn_apply_paid), TRUE, TRUE, 0);

  app->b_lbl_client_balance = GTK_LABEL(gtk_label_new("Saldo do cliente: —"));
  gtk_label_set_xalign(app->b_lbl_client_balance, 0.0);

  gtk_box_pack_start(GTK_BOX(pay), GTK_WIDGET(app->b_chk_paid), FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(pay), labeled(GTK_WIDGET(app->b_paid_amount), "Valor pago"), FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(pay), btns, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(pay), GTK_WIDGET(app->b_lbl_client_balance), FALSE, FALSE, 0);

  gtk_container_add(GTK_CONTAINER(pay_frame), pay);

  gtk_box_pack_start(GTK_BOX(right), list_frame, TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(right), pay_frame, FALSE, FALSE, 0);

  GtkWidget *right_sw = gtk_scrolled_window_new(NULL,NULL);
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(right_sw), GTK_POLICY_AUTOMATIC, GTK_POLICY_AUTOMATIC);
  gtk_container_add(GTK_CONTAINER(right_sw), right);

  gtk_box_pack_start(GTK_BOX(root), left, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(root), right_sw, TRUE, TRUE, 0);

  return root;
}

/* ----------------- App activate ----------------- */
static void activate(GtkApplication *gapp, gpointer user_data) {
  App *app = (App*)user_data;

  GtkSettings *settings = gtk_settings_get_default();
  if (settings) g_object_set(settings, "gtk-application-prefer-dark-theme", TRUE, NULL);

  apply_compact_css();

  app->win = GTK_WINDOW(gtk_application_window_new(gapp));
  gtk_window_set_title(app->win, "Agenda");
  gtk_window_set_default_size(app->win, 1250, 780);

  // ícone da janela
  {
    gchar *base = g_get_current_dir();
    gchar *icon_path = g_build_filename(base, APP_DATA_DIR, "logo.jpg", NULL);
    g_free(base);
    if (g_file_test(icon_path, G_FILE_TEST_EXISTS)) gtk_window_set_icon_from_file(app->win, icon_path, NULL);
    g_free(icon_path);
  }

  ensure_dirs_or_die(app->win);

  if (!app->clients) app->clients = g_ptr_array_new_with_free_func((GDestroyNotify)client_free);

  GtkWidget *root = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
  gtk_container_set_border_width(GTK_CONTAINER(root), 8);
  gtk_container_add(GTK_CONTAINER(app->win), root);

  gtk_box_pack_start(GTK_BOX(root), make_logo_header(app), FALSE, FALSE, 0);

  app->nb = GTK_NOTEBOOK(gtk_notebook_new());
  gtk_box_pack_start(GTK_BOX(root), GTK_WIDGET(app->nb), TRUE, TRUE, 0);

  GtkWidget *tab_agenda  = make_agenda_tab(app);
  GtkWidget *tab_clients = make_clients_tab(app);
  GtkWidget *tab_billing = make_billing_tab(app);

  app->page_agenda  = gtk_notebook_append_page(app->nb, tab_agenda,  gtk_label_new("Agenda"));
  app->page_clients = gtk_notebook_append_page(app->nb, tab_clients, gtk_label_new("Clientes"));
  app->page_billing = gtk_notebook_append_page(app->nb, tab_billing, gtk_label_new("Faturação"));

  load_all_clients(app);
  agenda_clients_combo_refresh(app);
  billing_clients_combo_refresh(app);

  // carregar agenda do dia inicial
  gchar *afile = agenda_file_for_calendar(app->a_cal);
  g_free(app->agenda_file_loaded);
  app->agenda_file_loaded = g_strdup(afile);
  agenda_load_file(app, afile);
  g_free(afile);

  client_form_clear(app);
  agenda_form_clear(app);

  // carregar faturação
  billing_load_file(app);
  billing_refilter(app);
  billing_update_totals(app);
  billing_update_selected_panel(app);

  gtk_widget_show_all(GTK_WIDGET(app->win));
}

/* ----------------- main ----------------- */
int main(int argc, char **argv) {
  App app;
  memset(&app, 0, sizeof(app));

  GtkApplication *gapp = gtk_application_new("com.exemplo.barbearia.txt", G_APPLICATION_DEFAULT_FLAGS);
  g_signal_connect(gapp, "activate", G_CALLBACK(activate), &app);

  int status = g_application_run(G_APPLICATION(gapp), argc, argv);

  g_clear_pointer(&app.picked_before_src, g_free);
  g_clear_pointer(&app.picked_after_src, g_free);
  g_clear_pointer(&app.agenda_file_loaded, g_free);
  g_clear_pointer(&app.filter_barber, g_free);
  g_clear_pointer(&app.filter_status, g_free);

  if (app.clients) g_ptr_array_free(app.clients, TRUE);

  g_object_unref(gapp);
  return status;
}
