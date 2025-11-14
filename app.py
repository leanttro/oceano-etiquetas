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
import uuid
import hashlib
# Importa o 'wraps' para criar o decorator de login
from functools import wraps

# Carrega variáveis de ambiente
load_dotenv()

# --- [CONFIGURAÇÃO UNIFICADA DO APP] ---
app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app, supports_credentials=True) 

# Configuração de sessão (Necessário para o login admin)
app.secret_key = os.getenv('SECRET_KEY', 'chave-secreta-padrao-trocar-em-prod')
ADMIN_SESSIONS = {} # Armazenamento de token em memória (simples)

# --- [INÍCIO: Bloco de Funções Utilitárias] ---

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

# JSON Encoder customizado para lidar com Decimal, Datas, etc.
class CustomJSONEncoder(json.JSONEncoder):
    """Codificador JSON customizado para o Flask."""
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        return super().default(obj)

app.json_encoder = CustomJSONEncoder

def format_db_data(data_dict):
    """(Função legada mantida) Formata dados do banco para JSON."""
    if not isinstance(data_dict, dict):
        return data_dict
    formatted_dict = {}
    for key, value in data_dict.items():
        if isinstance(value, (datetime.datetime, datetime.date)):
            formatted_dict[key] = value.isoformat() if value else None
        elif isinstance(value, decimal.Decimal):
            formatted_dict[key] = float(value)
        else:
            formatted_dict[key] = value
    return formatted_dict

# --- [FIM: Bloco de Funções Utilitárias] ---


# =====================================================================
# --- [INÍCIO: FUNCIONALIDADE PÚBLICA (Seu código original)] ---
# =====================================================================

@app.context_processor
def inject_dynamic_menu():
    """[Mantido] Injeta dados do menu em todos os templates renderizados."""
    conn = None
    categorias_ordem = ['Lacres', 'Adesivos', 'Brindes', 'Impressos']
    menu_data = collections.OrderedDict([(cat, []) for cat in categorias_ordem])

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # [CORREÇÃO 1] Removido 'WHERE esta_ativo = TRUE' para evitar o UndefinedColumn error
        query = """
            SELECT nome_produto, url_slug, categoria 
            FROM oceano_produtos 
            WHERE categoria IS NOT NULL AND categoria != '' 
              AND url_slug IS NOT NULL AND url_slug != ''
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

            produto_data = { 'nome': produto['nome_produto'], 'url': url_final_para_link }
            
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
    """[Mantido] API pública para listar produtos (usado na Home)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        categoria_filtro = request.args.get('categoria')
        
        # [CORREÇÃO 2] Removido 'WHERE esta_ativo = TRUE'
        query = "SELECT * FROM oceano_produtos"
        params = []

        if categoria_filtro:
            query += " WHERE categoria ILIKE %s"
            params.append(f"%{categoria_filtro}%")

        query += " ORDER BY codigo_produto;"
        cur.execute(query, tuple(params))
        produtos_raw = cur.fetchall()
        cur.close()
        
        return jsonify(produtos_raw)
        
    except psycopg2.errors.UndefinedTable:
        return jsonify({'error': 'Tabela oceano_produtos não encontrada.'}), 500
    except Exception as e:
        print(f"ERRO no endpoint /api/produtos: {e}")
        return jsonify({'error': 'Erro interno ao buscar produtos.'}), 500
    finally:
        if conn: conn.close()

@app.route('/produtos/<path:slug>') 
def produto_detalhe(slug):
    """[Mantido] Renderiza a página de detalhe de um produto."""
    conn = None
    try: 
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        url_busca_com_prefixo = f"/produtos/{slug}"
        
        # [CORREÇÃO 3] Removido 'AND esta_ativo = TRUE' de ambas as consultas
        cur.execute('SELECT * FROM oceano_produtos WHERE url_slug = %s;', (url_busca_com_prefixo,))
        produto = cur.fetchone()

        if not produto:
            cur.execute('SELECT * FROM oceano_produtos WHERE url_slug = %s;', (slug,))
            produto = cur.fetchone()

        cur.close()

        if produto:
            produto_formatado = dict(produto) 
            
            specs_json_string = produto_formatado.get('especificacoes_tecnicas')
            specs_dict = {} 
            if specs_json_string:
                try:
                    specs_dict = json.loads(specs_json_string)
                except json.JSONDecodeError:
                    print(f"AVISO: especs_tecnicas do slug '{slug}' não é JSON. Tratando como texto.")
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

# =====================================================================
# --- [FIM: FUNCIONALIDADE PÚBLICA] ---
# =====================================================================


# =====================================================================
# --- [INÍCIO: NOVO PAINEL ADMIN B2B] ---
# =====================================================================

@app.route('/admin')
def admin_panel():
    """[NOVO] Serve o painel de admin 'admin.html' da pasta 'templates'."""
    return render_template('admin.html')

@app.route('/api/oceano/admin/login', methods=['POST'])
def admin_login():
    """[NOVO] Faz o login consultando a tabela 'oceano_admin'."""
    data = request.json
    username = data.get('username')
    password = data.get('password') 

    if not username or not password:
        return jsonify({"erro": "Usuário e senha são obrigatórios"}), 400

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        cur.execute("SELECT * FROM oceano_admin WHERE username = %s AND chave_admin = %s", (username, password))
        admin_user = cur.fetchone()
        cur.close()

        if admin_user:
            token = str(uuid.uuid4())
            ADMIN_SESSIONS[token] = {"id": admin_user["id"], "username": admin_user["username"]}
            return jsonify({
                "mensagem": f"Login bem-sucedido! Bem-vindo, {admin_user['username']}.",
                "token": token
            })
        else:
            return jsonify({"erro": "Credenciais inválidas. Verifique usuário e senha."}), 401

    except Exception as e:
        print(f"ERRO em /api/oceano/admin/login: {e}")
        traceback.print_exc()
        return jsonify({"erro": "Erro interno no servidor de login."}), 500
    finally:
        if conn: conn.close()

# --- Wrapper de Verificação de Token ---
def token_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({"erro": "Token de autorização ausente"}), 401
        
        token = token.replace('Bearer ', '')
        
        if token not in ADMIN_SESSIONS:
            return jsonify({"erro": "Token inválido ou expirado"}), 401
        
        return f(*args, **kwargs)
    return decorated_function

# --- API de Dashboard (Stats) ---

@app.route('/api/oceano/admin/dashboard_stats', methods=['GET'])
@token_required
def get_dashboard_stats():
    """[NOVO] Busca estatísticas para o dashboard do admin."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # [CORREÇÃO 4] Removido 'WHERE esta_ativo = TRUE'
        cur.execute("SELECT COUNT(*) FROM oceano_produtos;")
        stat_produtos = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM oceano_clientes;")
        stat_clientes = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM oceano_pedidos WHERE status = 'Aguardando Orçamento' OR status = 'Aguardando Pagamento';")
        stat_pedidos = cur.fetchone()[0]
        
        cur.close()
        
        return jsonify({
            "stat_produtos": stat_produtos,
            "stat_clientes": stat_clientes,
            "stat_pedidos": stat_pedidos
        })
    except Exception as e:
        print(f"ERRO em /api/oceano/admin/dashboard_stats: {e}")
        return jsonify({"erro": "Erro ao buscar estatísticas."}), 500
    finally:
        if conn: conn.close()

# --- API CRUD: PRODUTOS (Substitui o Colab) ---

@app.route('/api/oceano/admin/produtos', methods=['GET', 'POST'])
@token_required
def handle_produtos():
    """[NOVO] Gerencia o CRUD da tabela 'oceano_produtos'."""
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        if request.method == 'GET':
            cur.execute("SELECT * FROM oceano_produtos ORDER BY nome_produto")
            produtos = cur.fetchall()
            cur.close()
            return jsonify(produtos)

        if request.method == 'POST':
            data = request.json
            
            galeria_list = data.get('galeria_imagens')
            if isinstance(galeria_list, str) and galeria_list.strip():
                 galeria_pg_array = [url.strip() for url in galeria_list.split(',') if url.strip()]
            else:
                galeria_pg_array = None

            # [CORREÇÃO 5] Removido 'esta_ativo' do INSERT
            cur.execute(
                """
                INSERT INTO oceano_produtos (
                    nome_produto, codigo_produto, url_slug, descricao_curta, 
                    descricao_longa, especificacoes_tecnicas, 
                    imagem_principal_url, imagem_principal_alt, galeria_imagens, 
                    categoria, subcategoria, meta_title, meta_description, 
                    whatsapp_link_texto, pagina_gerada
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) RETURNING *;
                """,
                (
                    data['nome_produto'], data.get('codigo_produto'), data.get('url_slug'),
                    data.get('descricao_curta'), data.get('descricao_longa'),
                    data.get('especificacoes_tecnicas'), 
                    data.get('imagem_principal_url'), data.get('imagem_principal_alt'),
                    galeria_pg_array, 
                    data.get('categoria'), data.get('subcategoria'),
                    data.get('meta_title'), data.get('meta_description'),
                    data.get('whatsapp_link_texto'), False 
                )
            )
            novo_produto = cur.fetchone()
            conn.commit()
            cur.close()
            return jsonify(novo_produto), 201

    except psycopg2.IntegrityError as e:
        conn.rollback()
        print(f"ERRO de Integridade (Produto): {e}")
        return jsonify({"erro": "Erro de integridade: Código do produto (SKU) ou URL (Slug) provavelmente já existem."}), 409
    except Exception as e:
        conn.rollback()
        print(f"ERRO em /api/oceano/admin/produtos: {e}")
        traceback.print_exc()
        return jsonify({"erro": "Erro interno no servidor."}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/produtos/<int:id>', methods=['GET', 'PUT', 'DELETE'])
@token_required
def handle_produto_id(id):
    """[NOVO] Gerencia GET(id), PUT(id), DELETE(id) para 'oceano_produtos'."""
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        if request.method == 'GET':
            cur.execute("SELECT * FROM oceano_produtos WHERE id = %s", (id,))
            produto = cur.fetchone()
            cur.close()
            if not produto:
                return jsonify({"erro": "Produto não encontrado"}), 404
            return jsonify(produto)

        if request.method == 'PUT':
            data = request.json
            
            galeria_list = data.get('galeria_imagens')
            if isinstance(galeria_list, str) and galeria_list.strip():
                 galeria_pg_array = [url.strip() for url in galeria_list.split(',') if url.strip()]
            else:
                galeria_pg_array = None

            # [CORREÇÃO 6] Removido 'esta_ativo' do UPDATE
            cur.execute(
                """
                UPDATE oceano_produtos SET
                    nome_produto = %s, codigo_produto = %s, url_slug = %s, 
                    descricao_curta = %s, descricao_longa = %s, 
                    especificacoes_tecnicas = %s, imagem_principal_url = %s, 
                    imagem_principal_alt = %s, galeria_imagens = %s, 
                    categoria = %s, subcategoria = %s, 
                    meta_title = %s, meta_description = %s, 
                    whatsapp_link_texto = %s
                WHERE id = %s
                RETURNING *;
                """,
                (
                    data['nome_produto'], data.get('codigo_produto'), data.get('url_slug'),
                    data.get('descricao_curta'), data.get('descricao_longa'),
                    data.get('especificacoes_tecnicas'), 
                    data.get('imagem_principal_url'), data.get('imagem_principal_alt'),
                    galeria_pg_array, 
                    data.get('categoria'), data.get('subcategoria'),
                    data.get('meta_title'), data.get('meta_description'),
                    data.get('whatsapp_link_texto'),
                    id
                )
            )
            produto_atualizado = cur.fetchone()
            conn.commit()
            cur.close()
            return jsonify(produto_atualizado)

        if request.method == 'DELETE':
            cur.execute("DELETE FROM oceano_produtos WHERE id = %s RETURNING *;", (id,))
            produto_deletado = cur.fetchone()
            conn.commit()
            cur.close()
            if not produto_deletado:
                return jsonify({"erro": "Produto não encontrado"}), 404
            return jsonify({"mensagem": f"Produto '{produto_deletado['nome_produto']}' deletado com sucesso"})

    except psycopg2.IntegrityError as e:
        conn.rollback()
        return jsonify({"erro": "Erro de integridade: Código (SKU) ou URL (Slug) duplicados."}), 409
    except Exception as e:
        conn.rollback()
        print(f"ERRO em /api/oceano/admin/produtos/{id}: {e}")
        traceback.print_exc()
        return jsonify({"erro": "Erro interno no servidor."}), 500
    finally:
        if conn: conn.close()

# --- API CRUD: PEDIDOS (Orçamentos) ---

@app.route('/api/oceano/admin/pedidos', methods=['GET'])
@token_required
def get_pedidos():
    """[NOVO] Lista todos os pedidos (orçamentos) para o admin."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(
            """
            SELECT p.*, c.nome_cliente 
            FROM oceano_pedidos p
            LEFT JOIN oceano_clientes c ON p.cliente_id = c.id
            ORDER BY p.data_criacao DESC
            """
        )
        pedidos = cur.fetchall()
        cur.close()
        return jsonify(pedidos)
    except Exception as e:
        print(f"ERRO em /api/oceano/admin/pedidos: {e}")
        return jsonify({"erro": "Erro ao buscar pedidos."}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/pedidos/<int:id>', methods=['GET', 'PUT'])
@token_required
def handle_pedido_id(id):
    """[NOVO] Busca detalhes de um pedido ou atualiza (status, valor, rastreio)."""
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        if request.method == 'GET':
            cur.execute(
                """
                SELECT p.*, c.nome_cliente, c.email, c.telefone 
                FROM oceano_pedidos p
                LEFT JOIN oceano_clientes c ON p.cliente_id = c.id
                WHERE p.id = %s
                """, (id,)
            )
            pedido = cur.fetchone()
            
            if not pedido:
                cur.close()
                return jsonify({"erro": "Pedido não encontrado"}), 404
            
            cur.execute(
                """
                SELECT i.*, p.nome_produto, p.codigo_produto 
                FROM oceano_pedido_itens i
                LEFT JOIN oceano_produtos p ON i.produto_id = p.id
                WHERE i.pedido_id = %s
                """, (id,)
            )
            itens = cur.fetchall()
            cur.close()
            
            pedido_completo = dict(pedido)
            pedido_completo['itens'] = [dict(item) for item in itens]
            return jsonify(pedido_completo)

        if request.method == 'PUT':
            data = request.json
            
            cur.execute(
                """
                UPDATE oceano_pedidos SET
                    status = %s,
                    valor_frete = %s,
                    valor_final_total = %s,
                    chave_pix = %s,
                    observacoes_admin = %s,
                    codigo_rastreio = %s,
                    data_atualizacao = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING *;
                """,
                (
                    data.get('status'),
                    data.get('valor_frete', 0.0),
                    data.get('valor_final_total', 0.0),
                    data.get('chave_pix'),
                    data.get('observacoes_admin'),
                    data.get('codigo_rastreio'),
                    id
                )
            )
            pedido_atualizado = cur.fetchone()
            
            if 'itens' in data and isinstance(data['itens'], list):
                for item in data['itens']:
                    cur.execute(
                        """
                        UPDATE oceano_pedido_itens SET
                            preco_unitario_definido = %s
                        WHERE id = %s AND pedido_id = %s;
                        """,
                        (item.get('preco_unitario_definido', 0.0), item.get('id'), id)
                    )
            
            conn.commit()
            cur.close()
            return jsonify(pedido_atualizado)

    except Exception as e:
        conn.rollback()
        print(f"ERRO em /api/oceano/admin/pedidos/{id}: {e}")
        traceback.print_exc()
        return jsonify({"erro": "Erro interno no servidor."}), 500
    finally:
        if conn: conn.close()

# --- API CRUD: CLIENTES ---

@app.route('/api/oceano/admin/clientes', methods=['GET', 'POST'])
@token_required
def handle_clientes():
    """[NOVO] Gerencia o CRUD da tabela 'oceano_clientes'."""
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        if request.method == 'GET':
            cur.execute("SELECT * FROM oceano_clientes ORDER BY nome_cliente")
            clientes = cur.fetchall()
            cur.close()
            return jsonify(clientes)

        if request.method == 'POST':
            data = request.json
            cur.execute(
                """
                INSERT INTO oceano_clientes (
                    nome_cliente, email, telefone, cnpj_cpf, codigo_acesso
                ) VALUES (%s, %s, %s, %s, %s) RETURNING *;
                """,
                (
                    data['nome_cliente'], data['email'], data.get('telefone'),
                    data.get('cnpj_cpf'), data['codigo_acesso']
                )
            )
            novo_cliente = cur.fetchone()
            conn.commit()
            cur.close()
            return jsonify(novo_cliente), 201

    except psycopg2.IntegrityError as e:
        conn.rollback()
        return jsonify({"erro": "Erro de integridade: Email, CNPJ/CPF ou Código de Acesso já existem."}), 409
    except Exception as e:
        conn.rollback()
        return jsonify({"erro": "Erro interno no servidor."}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/clientes/<int:id>', methods=['DELETE'])
@token_required
def handle_cliente_id(id):
    """[NOVO] Deleta um cliente."""
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("DELETE FROM oceano_clientes WHERE id = %s RETURNING *;", (id,))
        cliente_deletado = cur.fetchone()
        conn.commit()
        cur.close()
        if not cliente_deletado:
            return jsonify({"erro": "Cliente não encontrado"}), 404
        return jsonify({"mensagem": "Cliente deletado com sucesso"})
    except psycopg2.IntegrityError as e:
        conn.rollback()
        return jsonify({"erro": "Não é possível deletar este cliente pois ele possui pedidos associados."}), 409
    except Exception as e:
        conn.rollback()
        return jsonify({"erro": "Erro interno no servidor."}), 500
    finally:
        if conn: conn.close()

# --- API CRUD: ADMINS (Usuários) ---

@app.route('/api/oceano/admin/users', methods=['GET', 'POST'])
@token_required
def handle_admins():
    """[NOVO] Gerencia o CRUD da tabela 'oceano_admin'."""
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        if request.method == 'GET':
            cur.execute("SELECT id, username, data_criacao FROM oceano_admin ORDER BY username")
            admins = cur.fetchall()
            cur.close()
            return jsonify(admins)

        if request.method == 'POST':
            data = request.json
            cur.execute(
                "INSERT INTO oceano_admin (username, chave_admin) VALUES (%s, %s) RETURNING id, username, data_criacao;",
                (data['username'], data['chave_admin'])
            )
            novo_admin = cur.fetchone()
            conn.commit()
            cur.close()
            return jsonify(novo_admin), 201

    except psycopg2.IntegrityError as e:
        conn.rollback()
        return jsonify({"erro": "Erro de integridade: Nome de usuário já existe."}), 409
    except Exception as e:
        conn.rollback()
        return jsonify({"erro": "Erro interno no servidor."}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/users/<int:id>', methods=['DELETE'])
@token_required
def handle_admin_id(id):
    """[NOVO] Deleta um admin."""
    conn = get_db_connection()
    try:
        if id == 1:
            return jsonify({"erro": "Não é permitido deletar o administrador principal (ID 1)."}), 403

        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("DELETE FROM oceano_admin WHERE id = %s RETURNING *;", (id,))
        admin_deletado = cur.fetchone()
        conn.commit()
        cur.close()
        if not admin_deletado:
            return jsonify({"erro": "Administrador não encontrado"}), 404
        return jsonify({"mensagem": "Administrador deletado com sucesso"})
    except Exception as e:
        conn.rollback()
        return jsonify({"erro": "Erro interno no servidor."}), 500
    finally:
        if conn: conn.close()

# =====================================================================
# --- [FIM: NOVO PAINEL ADMIN B2B] ---
# =====================================================================


# --- ROTAS FINAIS (Servir o site público) ---

@app.route('/')
def index_route():
    """
    [Mantido] Renderiza o 'index.html' da pasta 'templates/'.
    """
    return render_template('index.html')

# A rota para servir arquivos estáticos (ex: /static/oceanologo.png)
# é tratada automaticamente pelo Flask porque definimos `static_folder='static'`.

# --- Execução do App ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)