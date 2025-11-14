import os
import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request, send_from_directory, render_template, make_response, session
from dotenv import load_dotenv
from flask_cors import CORS
import datetime
import traceback
import decimal
import json 
import collections 
import jwt # Importa JWT para tokens de login
from functools import wraps # Importa 'wraps' para os decoradores de login

# --- [NOVO] Importações do Chatbot ---
import google.generativeai as genai

# Carrega variáveis de ambiente
load_dotenv()

app = Flask(__name__, static_folder='static', static_url_path='/static', template_folder='templates')
CORS(app) 

# Configuração de Chave Secreta para JWT
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'sua-chave-secreta-padrao-mude-isso')

# --- [NOVO] Configuração do Gemini (Chatbot) ---
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
if not GEMINI_API_KEY:
    print("AVISO: GEMINI_API_KEY não encontrada. O Chatbot não funcionará.")
else:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        print("✅ [IA] Gemini configurado com sucesso.")
    except Exception as e:
        print(f"ERRO ao configurar Gemini: {e}")

# =====================================================================
# --- CONEXÃO COM BANCO E HELPERS ---
# =====================================================================

def get_db_connection():
    """Cria e retorna uma conexão com o banco de dados PostgreSQL."""
    conn = None
    try:
        db_url = os.getenv('DATABASE_URL')
        if not db_url:
            print("ERRO CRÍTICO: Variável de ambiente DATABASE_URL não encontrada.")
            raise ValueError("DATABASE_URL não configurada")
            
        conn = psycopg2.connect(db_url)
        return conn
    except Exception as e:
        print(f"ERRO CRÍTICO: Não foi possível conectar ao banco de dados: {e}")
        raise

def format_db_data(data_dict):
    """Formata dados do banco (datas, decimais) para serem compatíveis com JSON."""
    if not isinstance(data_dict, dict):
        return data_dict

    formatted_dict = {}
    for key, value in data_dict.items():
        if isinstance(value, (datetime.datetime, datetime.date)):
            formatted_dict[key] = value.isoformat() if value else None
        elif isinstance(value, datetime.time):
            formatted_dict[key] = value.strftime('%H:%M') if value else None
        elif isinstance(value, decimal.Decimal):
            try:
                formatted_dict[key] = float(value)
            except (TypeError, ValueError):
                formatted_dict[key] = None
        elif isinstance(value, list):
            formatted_dict[key] = value
        else:
            formatted_dict[key] = value
    return formatted_dict

# =====================================================================
# --- DECORADORES DE AUTENTICAÇÃO (Admin e Cliente) ---
# =====================================================================

def admin_token_required(f):
    """Decorador para rotas de ADMIN"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            token = request.headers['Authorization'].split(" ")[1]
        if not token:
            return jsonify({'erro': 'Token de admin está faltando!'}), 401
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            # Verifica se é um token de admin
            if 'admin_id' not in data:
                return jsonify({'erro': 'Token inválido (não é admin)!'}), 401
        except Exception as e:
            return jsonify({'erro': f'Erro no token de admin: {str(e)}'}), 401
        return f(*args, **kwargs)
    return decorated

def cliente_token_required(f):
    """Decorador para rotas de CLIENTE"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            token = request.headers['Authorization'].split(" ")[1]
        if not token:
            return jsonify({'erro': 'Token de cliente está faltando!'}), 401
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            # Passa o ID do cliente para a rota
            kwargs['cliente_id'] = data['cliente_id']
        except Exception as e:
            return jsonify({'erro': f'Erro no token de cliente: {str(e)}'}), 401
        return f(*args, **kwargs)
    return decorated


# =====================================================================
# --- PARTE 1: ROTAS PÚBLICAS (O Site 'oceano-etiquetas') ---
# (Funcionalidade 100% preservada)
# =====================================================================

@app.context_processor
def inject_dynamic_menu():
    """Injeta dados do menu em todos os templates renderizados."""
    conn = None
    categorias_ordem = ['Lacres', 'Adesivos', 'Brindes', 'Impressos']
    menu_data = collections.OrderedDict([(cat, []) for cat in categorias_ordem])
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        query = """
            SELECT nome_produto, url_slug, categoria 
            FROM oceano_produtos 
            WHERE categoria IS NOT NULL AND categoria != '' AND url_slug IS NOT NULL AND url_slug != ''
            ORDER BY categoria, nome_produto;
        """
        cur.execute(query)
        produtos = cur.fetchall()
        cur.close()
        for produto in produtos:
            cat = produto['categoria']
            slug_do_bd = produto['url_slug']
            if slug_do_bd.startswith('/produtos/'):
                slug_limpo = slug_do_bd[len('/produtos/'):]
            else:
                slug_limpo = slug_do_bd
            url_final_para_link = f"/produtos/{slug_limpo}"
            produto_data = {'nome': produto['nome_produto'], 'url': url_final_para_link}
            if cat in menu_data:
                menu_data[cat].append(produto_data)
            elif cat not in menu_data: 
                menu_data[cat] = [produto_data]
        menu_data_final = {k: v for k, v in menu_data.items() if v}
        return dict(menu_categorias=menu_data_final)
    except Exception as e:
        print(f"ERRO CRÍTICO ao gerar menu dinâmico: {e}")
        traceback.print_exc()
        return dict(menu_categorias=collections.OrderedDict())
    finally:
        if conn: conn.close()

@app.route('/api/produtos')
def get_api_produtos():
    """Retorna uma lista JSON de todos os produtos (usado pelo Portal do Cliente)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        query = "SELECT id, nome_produto, codigo_produto, categoria, imagem_principal_url, descricao_curta FROM oceano_produtos ORDER BY nome_produto;"
        cur.execute(query)
        produtos_raw = cur.fetchall()
        cur.close()
        produtos_processados = [format_db_data(dict(produto)) for produto in produtos_raw]
        return jsonify(produtos_processados)
    except Exception as e:
        print(f"ERRO no endpoint /api/produtos: {e}")
        return jsonify({'error': 'Erro interno ao buscar produtos.'}), 500
    finally:
        if conn: conn.close()

@app.route('/produtos/<path:slug>') 
def produto_detalhe(slug):
    """Renderiza a página de detalhe de um produto."""
    conn = None
    try: 
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        url_busca_com_prefixo = f"/produtos/{slug}"
        cur.execute('SELECT * FROM oceano_produtos WHERE url_slug = %s;', (url_busca_com_prefixo,))
        produto = cur.fetchone()
        if not produto:
            print(f"AVISO: Buscando slug legado por '{slug}'.")
            cur.execute('SELECT * FROM oceano_produtos WHERE url_slug = %s;', (slug,))
            produto = cur.fetchone()
        cur.close()
        if produto:
            produto_formatado = format_db_data(dict(produto))
            specs_json_string = produto_formatado.get('especificacoes_tecnicas')
            specs_dict = {} 
            if specs_json_string:
                try:
                    specs_dict = json.loads(specs_json_string)
                except json.JSONDecodeError:
                    specs_dict = {"Descrição": specs_json_string}
            produto_formatado['specs'] = specs_dict
            return render_template('oceano-produto-detalhe.html', produto=produto_formatado)
        else:
            return "Produto não encontrado", 404
    except Exception as e:
        print(f"ERRO na rota /produtos/{slug}: {e}")
        return "Erro ao carregar a página do produto", 500
    finally:
        if conn: conn.close()

@app.route('/')
def index_route():
    """Renderiza o 'index.html' dinamicamente."""
    return render_template('index.html')


# =====================================================================
# --- PARTE 2: ROTAS DO PAINEL ADMIN B2B ('/admin' e '/api/oceano/admin') ---
# (Funcionalidade 100% preservada)
# =====================================================================

@app.route('/admin')
def admin_panel_route():
    """Serve a página HTML do painel de administração."""
    return render_template('admin.html')

@app.route('/api/oceano/admin/login', methods=['POST'])
def admin_login():
    """Verifica o login do admin na tabela 'oceano_admin'."""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM oceano_admin WHERE username = %s", (username,))
        admin_user = cur.fetchone()
        cur.close()
        if admin_user and admin_user['chave_admin'] == password:
            token = jwt.encode({
                'admin_id': admin_user['id'],
                'username': admin_user['username'],
                'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
            }, app.config['SECRET_KEY'], algorithm="HS256")
            return jsonify({'mensagem': 'Login bem-sucedido!', 'token': token})
        else:
            return jsonify({'erro': 'Credenciais inválidas. Verifique usuário e senha.'}), 401
    except Exception as e:
        print(f"ERRO no login admin: {e}")
        return jsonify({'erro': 'Erro interno no servidor.'}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/dashboard_stats', methods=['GET'])
@admin_token_required
def get_dashboard_stats():
    """Coleta estatísticas para os cards do admin."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(id) FROM oceano_orcamentos WHERE status = 'Aguardando Orçamento'")
        stat_orcamentos = cur.fetchone()[0]
        cur.execute("SELECT COUNT(id) FROM oceano_pedidos WHERE status = 'Em Produção'")
        stat_pedidos = cur.fetchone()[0]
        cur.execute("SELECT COUNT(id) FROM oceano_produtos")
        stat_produtos = cur.fetchone()[0]
        cur.close()
        return jsonify({
            'stat_orcamentos': stat_orcamentos,
            'stat_pedidos': stat_pedidos,
            'stat_produtos': stat_produtos,
            # stat_clientes não existe no admin V3, foi removido do dashboard
        })
    except Exception as e:
        print(f"ERRO ao buscar stats: {e}")
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

# --- [CRUD PRODUTOS (Admin)] ---
@app.route('/api/oceano/admin/produtos', methods=['GET', 'POST'])
@admin_token_required
def handle_produtos():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if request.method == 'GET':
            cur.execute("SELECT id, nome_produto, codigo_produto, categoria, imagem_principal_url FROM oceano_produtos ORDER BY id DESC")
            produtos = [format_db_data(dict(p)) for p in cur.fetchall()]
            cur.close()
            return jsonify(produtos)
        if request.method == 'POST':
            data = request.get_json()
            galeria_list = [url.strip() for url in data.get('galeria_imagens', '').split(',') if url.strip()] or None
            sql = """
            INSERT INTO oceano_produtos (
                nome_produto, codigo_produto, whatsapp_link_texto, descricao_curta, 
                descricao_longa, especificacoes_tecnicas, imagem_principal_url, 
                imagem_principal_alt, galeria_imagens, categoria, subcategoria, 
                url_slug, meta_title, meta_description
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;
            """
            cur.execute(sql, (
                data.get('nome_produto'), data.get('codigo_produto'), data.get('whatsapp_link_texto'),
                data.get('descricao_curta'), data.get('descricao_longa'), data.get('especificacoes_tecnicas'),
                data.get('imagem_principal_url'), data.get('imagem_principal_alt'), galeria_list,
                data.get('categoria'), data.get('subcategoria'), data.get('url_slug'),
                data.get('meta_title'), data.get('meta_description')
            ))
            novo_id = cur.fetchone()['id']
            conn.commit()
            cur.close()
            return jsonify({'mensagem': f'Produto ID {novo_id} criado com sucesso!', 'id': novo_id}), 201
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/produtos/<int:id>', methods=['GET', 'PUT', 'DELETE'])
@admin_token_required
def handle_produto_id(id):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if request.method == 'GET':
            cur.execute("SELECT * FROM oceano_produtos WHERE id = %s", (id,))
            produto = cur.fetchone()
            if not produto: return jsonify({'erro': 'Produto não encontrado'}), 404
            cur.close()
            return jsonify(format_db_data(dict(produto)))
        if request.method == 'PUT':
            data = request.get_json()
            galeria_list = [url.strip() for url in data.get('galeria_imagens', '').split(',') if url.strip()] or None
            sql = """
            UPDATE oceano_produtos SET
                nome_produto = %s, codigo_produto = %s, whatsapp_link_texto = %s, 
                descricao_curta = %s, descricao_longa = %s, especificacoes_tecnicas = %s, 
                imagem_principal_url = %s, imagem_principal_alt = %s, galeria_imagens = %s, 
                categoria = %s, subcategoria = %s, url_slug = %s, 
                meta_title = %s, meta_description = %s
            WHERE id = %s;
            """
            cur.execute(sql, (
                data.get('nome_produto'), data.get('codigo_produto'), data.get('whatsapp_link_texto'),
                data.get('descricao_curta'), data.get('descricao_longa'), data.get('especificacoes_tecnicas'),
                data.get('imagem_principal_url'), data.get('imagem_principal_alt'), galeria_list,
                data.get('categoria'), data.get('subcategoria'), data.get('url_slug'),
                data.get('meta_title'), data.get('meta_description'), id
            ))
            conn.commit()
            cur.close()
            return jsonify({'mensagem': f'Produto ID {id} atualizado com sucesso!'})
        if request.method == 'DELETE':
            cur.execute("DELETE FROM oceano_produtos WHERE id = %s", (id,))
            conn.commit()
            cur.close()
            return jsonify({'mensagem': f'Produto ID {id} excluído com sucesso!'})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

# --- [CRUD CLIENTES (Admin)] ---
@app.route('/api/oceano/admin/clientes', methods=['GET', 'POST'])
@admin_token_required
def handle_clientes():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if request.method == 'GET':
            cur.execute("SELECT * FROM oceano_clientes ORDER BY nome_cliente")
            clientes = [format_db_data(dict(c)) for c in cur.fetchall()]
            cur.close()
            return jsonify(clientes)
        if request.method == 'POST':
            data = request.get_json()
            sql = "INSERT INTO oceano_clientes (nome_cliente, email, telefone, cnpj_cpf, codigo_acesso) VALUES (%s, %s, %s, %s, %s) RETURNING id;"
            cur.execute(sql, (data.get('nome_cliente'), data.get('email'), data.get('telefone'), data.get('cnpj_cpf'), data.get('codigo_acesso')))
            novo_id = cur.fetchone()['id']
            conn.commit()
            cur.close()
            return jsonify({'mensagem': 'Cliente criado com sucesso!', 'id': novo_id}), 201
    except psycopg2.IntegrityError as e:
        if conn: conn.rollback()
        return jsonify({'erro': f'Erro de integridade: {e.pgerror}'}), 409
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/clientes/<int:id>', methods=['DELETE'])
@admin_token_required
def handle_cliente_id(id):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM oceano_clientes WHERE id = %s", (id,))
        conn.commit()
        cur.close()
        return jsonify({'mensagem': f'Cliente ID {id} excluído com sucesso!'})
    except psycopg2.Error as e:
        if conn: conn.rollback()
        if e.pgcode == '23503': 
            return jsonify({'erro': 'Não é possível excluir: este cliente já possui orçamentos ou pedidos registrados.'}), 409
        return jsonify({'erro': f'Erro de DB: {e.pgerror}'}), 500
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

# --- [CRUD ADMINS (Admin)] ---
@app.route('/api/oceano/admin/users', methods=['GET', 'POST'])
@admin_token_required
def handle_admins():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if request.method == 'GET':
            cur.execute("SELECT id, username, data_criacao FROM oceano_admin ORDER BY id")
            admins = [format_db_data(dict(a)) for a in cur.fetchall()]
            cur.close()
            return jsonify(admins)
        if request.method == 'POST':
            data = request.get_json()
            sql = "INSERT INTO oceano_admin (username, chave_admin) VALUES (%s, %s) RETURNING id;"
            cur.execute(sql, (data.get('username'), data.get('chave_admin')))
            novo_id = cur.fetchone()['id']
            conn.commit()
            cur.close()
            return jsonify({'mensagem': 'Admin criado com sucesso!', 'id': novo_id}), 201
    except psycopg2.IntegrityError:
        if conn: conn.rollback()
        return jsonify({'erro': 'Este nome de usuário já existe.'}), 409
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/users/<int:id>', methods=['DELETE'])
@admin_token_required
def handle_admin_id(id):
    if id == 1:
        return jsonify({'erro': 'Não é possível excluir o administrador root (ID 1).'}), 403
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM oceano_admin WHERE id = %s", (id,))
        conn.commit()
        cur.close()
        return jsonify({'mensagem': f'Admin ID {id} excluído com sucesso!'})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

# --- [API ORÇAMENTOS (Admin)] ---
@app.route('/api/oceano/admin/orcamentos', methods=['GET'])
@admin_token_required
def get_orcamentos():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
        SELECT o.*, c.nome_cliente 
        FROM oceano_orcamentos o LEFT JOIN oceano_clientes c ON o.cliente_id = c.id
        WHERE o.status NOT IN ('Convertido em Pedido', 'Cancelado')
        ORDER BY o.data_atualizacao DESC;
        """
        cur.execute(sql)
        orcamentos = [format_db_data(dict(o)) for o in cur.fetchall()]
        cur.close()
        return jsonify(orcamentos)
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/orcamentos/<int:id>', methods=['GET', 'PUT'])
@admin_token_required
def handle_orcamento_id(id):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if request.method == 'GET':
            orcamento = {}
            sql_orc = "SELECT o.*, c.nome_cliente, c.email FROM oceano_orcamentos o LEFT JOIN oceano_clientes c ON o.cliente_id = c.id WHERE o.id = %s;"
            cur.execute(sql_orc, (id,))
            orcamento_data = cur.fetchone()
            if not orcamento_data:
                return jsonify({'erro': 'Orçamento não encontrado'}), 404
            orcamento = format_db_data(dict(orcamento_data))
            sql_itens = "SELECT oi.*, p.nome_produto, p.codigo_produto FROM oceano_orcamento_ilens oi LEFT JOIN oceano_produtos p ON oi.produto_id = p.id WHERE oi.orcamento_id = %s ORDER BY oi.id;"
            cur.execute(sql_itens, (id,))
            itens_data = cur.fetchall()
            orcamento['itens'] = [format_db_data(dict(i)) for i in itens_data]
            cur.close()
            return jsonify(orcamento)
        if request.method == 'PUT':
            data = request.get_json()
            itens_atualizados = data.get('itens', [])
            cur.execute("BEGIN;")
            sql_update_orc = """
            UPDATE oceano_orcamentos SET
                status = %s, valor_frete = %s, valor_final_total = %s,
                chave_pix = %s, observacoes_admin = %s, data_atualizacao = CURRENT_TIMESTAMP
            WHERE id = %s;
            """
            cur.execute(sql_update_orc, (data.get('status'), data.get('valor_frete'), data.get('valor_final_total'), data.get('chave_pix'), data.get('observacoes_admin'), id))
            sql_update_item = "UPDATE oceano_orcamento_ilens SET preco_unitario_definido = %s WHERE id = %s AND orcamento_id = %s"
            for item in itens_atualizados:
                cur.execute(sql_update_item, (item.get('preco_unitario_definido'), item.get('id'), id))
            conn.commit()
            cur.close()
            return jsonify({'mensagem': 'Orçamento atualizado com sucesso!'})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/orcamentos/<int:id>/aprovar', methods=['POST'])
@admin_token_required
def aprovar_orcamento(id):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("BEGIN;")
        cur.execute("SELECT * FROM oceano_orcamentos WHERE id = %s", (id,))
        orcamento = cur.fetchone()
        if not orcamento:
            return jsonify({'erro': 'Orçamento não encontrado'}), 404
        cur.execute("SELECT * FROM oceano_orcamento_ilens WHERE orcamento_id = %s", (id,))
        itens_orcamento = cur.fetchall()
        sql_insert_pedido = "INSERT INTO oceano_pedidos (cliente_id, status, valor_frete, valor_final_total, chave_pix, observacoes_admin, data_criacao, data_atualizacao) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP) RETURNING id;"
        cur.execute(sql_insert_pedido, (orcamento['cliente_id'], 'Em Produção', orcamento['valor_frete'], orcamento['valor_final_total'], orcamento['chave_pix'], orcamento['observacoes_admin'], orcamento['data_criacao']))
        novo_pedido_id = cur.fetchone()['id']
        sql_insert_item_pedido = "INSERT INTO oceano_pedido_ilens (pedido_id, produto_id, quantidade_solicitada, observacoes_cliente, preco_unitario_definido) VALUES (%s, %s, %s, %s, %s);"
        for item in itens_orcamento:
            cur.execute(sql_insert_item_pedido, (novo_pedido_id, item['produto_id'], item['quantidade_solicitada'], item['observacoes_cliente'], item['preco_unitario_definido']))
        cur.execute("UPDATE oceano_orcamentos SET status = 'Convertido em Pedido' WHERE id = %s", (id,))
        conn.commit()
        cur.close()
        return jsonify({'mensagem': f'Orçamento {id} aprovado e convertido no Pedido #{novo_pedido_id}!'})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

# --- [API PEDIDOS (Admin)] ---
@app.route('/api/oceano/admin/pedidos', methods=['GET'])
@admin_token_required
def get_pedidos():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = "SELECT p.*, c.nome_cliente FROM oceano_pedidos p LEFT JOIN oceano_clientes c ON p.cliente_id = c.id ORDER BY p.data_atualizacao DESC;"
        cur.execute(sql)
        pedidos = [format_db_data(dict(p)) for p in cur.fetchall()]
        cur.close()
        return jsonify(pedidos)
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/pedidos/<int:id>', methods=['GET', 'PUT'])
@admin_token_required
def handle_pedido_id(id):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if request.method == 'GET':
            pedido = {}
            sql_ped = "SELECT p.*, c.nome_cliente, c.email FROM oceano_pedidos p LEFT JOIN oceano_clientes c ON p.cliente_id = c.id WHERE p.id = %s;"
            cur.execute(sql_ped, (id,))
            pedido_data = cur.fetchone()
            if not pedido_data:
                return jsonify({'erro': 'Pedido não encontrado'}), 404
            pedido = format_db_data(dict(pedido_data))
            sql_itens = "SELECT pi.*, p.nome_produto, p.codigo_produto FROM oceano_pedido_ilens pi LEFT JOIN oceano_produtos p ON pi.produto_id = p.id WHERE pi.pedido_id = %s ORDER BY pi.id;"
            cur.execute(sql_itens, (id,))
            itens_data = cur.fetchall()
            pedido['itens'] = [format_db_data(dict(i)) for i in itens_data]
            cur.close()
            return jsonify(pedido)
        if request.method == 'PUT':
            data = request.get_json()
            sql_update_ped = "UPDATE oceano_pedidos SET status = %s, codigo_rastreio = %s, observacoes_admin = %s, data_atualizacao = CURRENT_TIMESTAMP WHERE id = %s;"
            cur.execute(sql_update_ped, (data.get('status'), data.get('codigo_rastreio'), data.get('observacoes_admin'), id))
            conn.commit()
            cur.close()
            return jsonify({'mensagem': 'Pedido atualizado com sucesso!'})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()


# =====================================================================
# --- [NOVO] PARTE 3: ROTAS DO PORTAL DO CLIENTE ---
# =====================================================================

@app.route('/portal')
def cliente_portal_route():
    """Serve a página HTML do portal do cliente."""
    return render_template('cliente.html')

@app.route('/api/oceano/cliente/login', methods=['POST'])
def cliente_login():
    """Verifica o login do cliente (código de acesso)."""
    data = request.get_json()
    codigo_acesso = data.get('codigo_acesso')
    if not codigo_acesso:
        return jsonify({'erro': 'Código de acesso é obrigatório'}), 400
    
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT id, nome_cliente FROM oceano_clientes WHERE codigo_acesso = %s", (codigo_acesso,))
        cliente = cur.fetchone()
        cur.close()
        
        if cliente:
            token = jwt.encode({
                'cliente_id': cliente['id'],
                'nome_cliente': cliente['nome_cliente'],
                'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=72) # Token de cliente dura 3 dias
            }, app.config['SECRET_KEY'], algorithm="HS256")
            return jsonify({
                'mensagem': 'Login bem-sucedido!', 
                'token': token,
                'cliente_id': cliente['id'],
                'nome_cliente': cliente['nome_cliente']
            })
        else:
            return jsonify({'erro': 'Código de acesso inválido.'}), 401
    except Exception as e:
        print(f"ERRO no login cliente: {e}")
        return jsonify({'erro': 'Erro interno no servidor.'}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/cliente/dashboard', methods=['GET'])
@cliente_token_required
def get_cliente_dashboard(cliente_id):
    """Coleta estatísticas para o dashboard do cliente."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Orçamentos aguardando pagamento
        cur.execute("SELECT COUNT(id) FROM oceano_orcamentos WHERE cliente_id = %s AND status = 'Aguardando Pagamento'", (cliente_id,))
        stat_aguardando_pagamento = cur.fetchone()[0]
        
        # Pedidos em produção
        cur.execute("SELECT COUNT(id) FROM oceano_pedidos WHERE cliente_id = %s AND status = 'Em Produção'", (cliente_id,))
        stat_em_producao = cur.fetchone()[0]
        
        # Pedidos enviados/prontos
        cur.execute("SELECT COUNT(id) FROM oceano_pedidos WHERE cliente_id = %s AND (status = 'Enviado' OR status = 'Pronto para Retirada')", (cliente_id,))
        stat_prontos = cur.fetchone()[0]
        
        cur.close()
        return jsonify({
            'stat_aguardando_pagamento': stat_aguardando_pagamento,
            'stat_em_producao': stat_em_producao,
            'stat_prontos': stat_prontos
        })
    except Exception as e:
        print(f"ERRO ao buscar stats do cliente: {e}")
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/cliente/orcamentos', methods=['GET'])
@cliente_token_required
def get_cliente_orcamentos(cliente_id):
    """Lista TODOS os orçamentos e pedidos de um cliente."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # 1. Pega Orçamentos pendentes
        sql_orc = "SELECT id, 'orcamento' as tipo, data_criacao, data_atualizacao, status, valor_final_total, chave_pix, codigo_rastreio, observacoes_admin FROM oceano_orcamentos WHERE cliente_id = %s"
        # 2. Pega Pedidos aprovados
        sql_ped = "SELECT id, 'pedido' as tipo, data_criacao, data_atualizacao, status, valor_final_total, chave_pix, codigo_rastreio, observacoes_admin FROM oceano_pedidos WHERE cliente_id = %s"
        
        # Une os dois e ordena pela data mais recente
        sql_union = f"({sql_orc}) UNION ALL ({sql_ped}) ORDER BY data_atualizacao DESC"
        
        cur.execute(sql_union, (cliente_id, cliente_id))
        
        documentos = [format_db_data(dict(doc)) for doc in cur.fetchall()]
        cur.close()
        return jsonify(documentos)
        
    except Exception as e:
        print(f"ERRO ao buscar orçamentos/pedidos do cliente: {e}")
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/cliente/orcamentos/novo', methods=['POST'])
@cliente_token_required
def post_novo_orcamento(cliente_id):
    """Cria um novo orçamento e seus itens."""
    data = request.get_json()
    itens = data.get('itens')
    if not itens or not isinstance(itens, list) or len(itens) == 0:
        return jsonify({'erro': 'O orçamento deve ter pelo menos um item.'}), 400
        
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("BEGIN;")
        
        # 1. Cria o Orçamento "capa"
        sql_orc = "INSERT INTO oceano_orcamentos (cliente_id, status, data_criacao, data_atualizacao) VALUES (%s, 'Aguardando Orçamento', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) RETURNING id;"
        cur.execute(sql_orc, (cliente_id,))
        novo_orcamento_id = cur.fetchone()['id']
        
        # 2. Insere os Itens
        sql_item = "INSERT INTO oceano_orcamento_ilens (orcamento_id, produto_id, quantidade_solicitada, observacoes_cliente) VALUES (%s, %s, %s, %s);"
        for item in itens:
            cur.execute(sql_item, (
                novo_orcamento_id,
                item.get('produto_id'),
                item.get('quantidade'),
                item.get('observacao')
            ))
            
        conn.commit()
        cur.close()
        return jsonify({'mensagem': f'Orçamento #{novo_orcamento_id} solicitado com sucesso! Entraremos em contato em breve.', 'orcamento_id': novo_orcamento_id}), 201
        
    except Exception as e:
        if conn: conn.rollback()
        print(f"ERRO ao criar novo orçamento: {e}")
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

# =====================================================================
# --- [NOVO] PARTE 4: API DO CHATBOT ---
# =====================================================================

# --- Ferramentas do Chatbot ---
def tool_check_status_pedido(pedido_id_str, cliente_id):
    """Ferramenta: Busca o status de um pedido ou orçamento no banco de dados."""
    print(f"[Chatbot Tool] Verificando Pedido/Orçamento ID {pedido_id_str} para Cliente {cliente_id}")
    try:
        pedido_id = int(pedido_id_str)
    except ValueError:
        return json.dumps({"erro": "ID do pedido inválido. Deve ser um número."})
    
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Tenta buscar em Orçamentos primeiro
        cur.execute("SELECT status, valor_final_total, chave_pix, observacoes_admin FROM oceano_orcamentos WHERE id = %s AND cliente_id = %s", (pedido_id, cliente_id))
        doc = cur.fetchone()
        tipo = "Orçamento"
        
        # Se não achar, tenta em Pedidos
        if not doc:
            cur.execute("SELECT status, valor_final_total, codigo_rastreio, observacoes_admin FROM oceano_pedidos WHERE id = %s AND cliente_id = %s", (pedido_id, cliente_id))
            doc = cur.fetchone()
            tipo = "Pedido"

        cur.close()
        
        if doc:
            doc_formatado = format_db_data(dict(doc))
            doc_formatado['tipo'] = tipo
            return json.dumps(doc_formatado)
        else:
            return json.dumps({"erro": f"Nenhum orçamento ou pedido com o ID {pedido_id} foi encontrado para este cliente."})
            
    except Exception as e:
        print(f"ERRO na ferramenta check_status_pedido: {e}")
        return json.dumps({"erro": "Erro interno ao consultar o banco de dados."})
    finally:
        if conn: conn.close()

# --- Configuração do Modelo Gemini ---
if GEMINI_API_KEY:
    # Definição das ferramentas que a IA pode usar
    # MUDANÇA: A Google Search Tool deve ser listada separadamente.
    tools_to_use = [
        "google_search",  # Nome da ferramenta Google Search
        {
            "function_declarations": [
                {
                    "name": "check_status_pedido",
                    "description": "Verifica o status de um orçamento ou pedido existente usando o ID.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "pedido_id": {"type": "STRING", "description": "O ID (número) do orçamento ou pedido. Ex: 123"}
                        },
                        "required": ["pedido_id"]
                    }
                }
            ]
        }
    ]
    
    # O "cérebro" do chatbot
    SYSTEM_PROMPT = """
    Você é o 'Oceano Bot', o assistente de vendas e atendimento da Oceano Etiquetas.
    Seu único objetivo é ajudar clientes e vender produtos, baseando-se **estritamente** em informações do site www.oceanoetiquetas.com.br e nos dados do sistema interno.

    REGRAS PRINCIPAIS:
    1.  **VENDAS (GROUNDING):** Para qualquer pergunta sobre produtos, materiais (VOID, BOPP, couchê, PVC), ou sobre a empresa, você SÓ PODE usar a ferramenta Google Search para pesquisar em `www.oceanoetiquetas.com.br`.
    2.  **ATENDIMENTO (TOOLS):** Para perguntas sobre "meu pedido", "status", "rastreio", "preço do meu orçamento", você SÓ PODE usar a ferramenta `check_status_pedido`.
    3.  **TOM DE VOZ:** Seja profissional, prestativo e técnico. Você é um especialista em etiquetas.
    4.  **SEGURANÇA:** NUNCA forneça informações de um pedido a menos que o cliente pergunte e a ferramenta `check_status_pedido` retorne os dados (a ferramenta já filtra pelo ID do cliente).
    5.  **LIMITAÇÃO:** Se a informação não estiver no site ou nas ferramentas, diga "Não encontrei essa informação nos nossos sistemas ou no site www.oceanoetiquetas.com.br". Não invente.
    """
    
    # Inicializa o modelo
    gemini_model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-preview-09-2025",
        system_instruction=SYSTEM_PROMPT,
        tools=tools_to_use # MUDANÇA: Passa a lista `tools_to_use`
    )
else:
    gemini_model = None

@app.route('/api/oceano/chat', methods=['POST'])
@cliente_token_required
def handle_chat(cliente_id):
    if not gemini_model:
        return jsonify({'response': 'Desculpe, a Inteligência Artificial não está configurada. (GEMINI_API_KEY não encontrada).'}), 500

    data = request.get_json()
    message = data.get('message')
    history_raw = data.get('history', [])
    
    # Constrói o histórico para o Gemini
    chat_history = []
    for item in history_raw:
        chat_history.append({'role': item['role'], 'parts': [{'text': item['content']}]})

    # [GROUNDING] Adiciona o prefixo do site para o Google Search
    grounded_message = f"site:www.oceanoetiquetas.com.br {message}"

    try:
        # Inicia o chat
        chat = gemini_model.start_chat(history=chat_history)
        
        # 1. Envia a mensagem do usuário (com grounding)
        response = chat.send_message(grounded_message)
        
        # 2. Verifica se a IA quer usar uma ferramenta
        while response.candidates[0].content.parts[0].function_call:
            function_call = response.candidates[0].content.parts[0].function_call
            
            tool_result = None
            if function_call.name == "check_status_pedido":
                args = function_call.args
                pedido_id = args.get('pedido_id')
                # Chama a ferramenta com o ID do cliente logado (para segurança)
                tool_result_json = tool_check_status_pedido(pedido_id, cliente_id)
                tool_result = json.loads(tool_result_json)
            
            # 3. Envia o resultado da ferramenta de volta para a IA
            if tool_result:
                response = chat.send_message(
                    part=genai.Part(
                        function_response=genai.FunctionResponse(
                            name=function_call.name,
                            response={"result": tool_result}
                        )
                    )
                )
            else:
                # Se a ferramenta falhar, envia uma resposta genérica
                response = chat.send_message(
                    part=genai.Part(
                        function_response=genai.FunctionResponse(
                            name=function_call.name,
                            response={"result": {"erro": "Ferramenta não reconhecida."}}
                        )
                    )
                )
        
        # 4. Retorna a resposta final da IA (em texto)
        final_response_text = response.candidates[0].content.parts[0].text
        return jsonify({'response': final_response_text})

    except Exception as e:
        print(f"🔴 Erro Chatbot API: {e}")
        traceback.print_exc()
        return jsonify({"response": "Desculpe, tive um problema interno ao processar sua solicitação."}), 500


# =====================================================================
# --- PARTE 5: ROTAS PÚBLICAS (Fallback) ---
# (Deve vir por último)
# =====================================================================

@app.route('/<path:path>')
def serve_static_or_404(path):
    """
    Serve arquivos da pasta 'static/' (que agora é a pasta estática definida no Flask)
    ou retorna 404 se não for uma rota de API conhecida.
    """
    # Esta função só será chamada se a rota não for
    # '/', '/admin', '/portal', '/produtos/<slug>', ou '/api/...'
    
    print(f"AVISO: Rota não encontrada (404) para: {path}")
    return "Página não encontrada", 404

# --- Execução do App ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    # Mude debug=True para desenvolvimento local
    app.run(host="0.0.0.0", port=port, debug=False)