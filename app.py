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

# Carrega variáveis de ambiente
load_dotenv()

app = Flask(__name__, static_folder='static', static_url_path='/static', template_folder='templates')
CORS(app) 

# Configuração de Chave Secreta para JWT
# MUITO IMPORTANTE: Mude isso no Render para uma string aleatória e segura!
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'sua-chave-secreta-padrao-mude-isso')

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
            # Lida com listas (ex: galeria_imagens)
            formatted_dict[key] = value
        else:
            formatted_dict[key] = value
    return formatted_dict

# =====================================================================
# --- DECORADOR DE AUTENTICAÇÃO ADMIN (JWT) ---
# =====================================================================

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            # Padrão: "Bearer <token>"
            token = request.headers['Authorization'].split(" ")[1]

        if not token:
            return jsonify({'erro': 'Token de autenticação está faltando!'}), 401

        try:
            # Verifica o token usando a SECRET_KEY
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            # Você pode opcionalmente verificar se o admin_id do token ainda existe no DB
            # current_admin = ...
        except jwt.ExpiredSignatureError:
            return jsonify({'erro': 'Token expirou!'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'erro': 'Token inválido!'}), 401
        except Exception as e:
            return jsonify({'erro': f'Erro no token: {str(e)}'}), 401

        return f(*args, **kwargs)
    return decorated


# =====================================================================
# --- PARTE 1: ROTAS PÚBLICAS (O Site 'oceano-etiquetas') ---
# (Nenhuma funcionalidade foi removida)
# =====================================================================

@app.context_processor
def inject_dynamic_menu():
    """
    Injeta dados do menu em todos os templates renderizados.
    Consulta o BD e agrupa os produtos por categoria.
    """
    conn = None
    categorias_ordem = ['Lacres', 'Adesivos', 'Brindes', 'Impressos']
    menu_data = collections.OrderedDict([(cat, []) for cat in categorias_ordem])

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # [CORREÇÃO V2] Removeu o 'esta_ativo' para evitar crash
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

            produto_data = {
                'nome': produto['nome_produto'],
                'url': url_final_para_link 
            }
            
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
    """Retorna uma lista JSON de todos os produtos da tabela 'oceano_produtos'."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        categoria_filtro = request.args.get('categoria')
        
        # [CORREÇÃO V2] Removeu o 'esta_ativo'
        query = "SELECT * FROM oceano_produtos"
        params = []

        if categoria_filtro:
            query += " WHERE categoria ILIKE %s"
            params.append(f"%{categoria_filtro}%")

        query += " ORDER BY codigo_produto;"
        
        cur.execute(query, tuple(params))
        produtos_raw = cur.fetchall()
        cur.close()

        produtos_processados = [format_db_data(dict(produto)) for produto in produtos_raw]
        return jsonify(produtos_processados)
        
    except psycopg2.errors.UndefinedTable:
        print("ERRO: A tabela 'oceano_produtos' não foi encontrada no banco de dados.")
        return jsonify({'error': 'Tabela oceano_produtos não encontrada.'}), 500
    except Exception as e:
        print(f"ERRO no endpoint /api/produtos: {e}")
        return jsonify({'error': 'Erro interno ao buscar produtos.'}), 500
    finally:
        if conn: conn.close()

@app.route('/produtos/<path:slug>') 
def produto_detalhe(slug):
    """Renderiza a página de detalhe de um produto buscando pelo 'url_slug'."""
    conn = None
    try: 
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        url_busca_com_prefixo = f"/produtos/{slug}"
        
        # [CORREÇÃO V2] Removeu o 'esta_ativo'
        cur.execute('SELECT * FROM oceano_produtos WHERE url_slug = %s;', (url_busca_com_prefixo,))
        produto = cur.fetchone()

        if not produto:
            print(f"AVISO: Produto com slug/url '{url_busca_com_prefixo}' não encontrado. Tentando busca legada por '{slug}'.")
            cur.execute('SELECT * FROM oceano_produtos WHERE url_slug = %s;', (slug,))
            produto = cur.fetchone()
        
        cur.close()

        if produto:
            produto_formatado = format_db_data(dict(produto))
            specs_json_string = produto_formatado.get('especificacoes_tecnicas')
            specs_dict = {} 
            
            if specs_json_string:
                try:
                    # Tenta carregar o JSON
                    specs_dict = json.loads(specs_json_string)
                except json.JSONDecodeError:
                    # Se falhar (ex: texto simples), trata como texto
                    print(f"AVISO: Falha ao decodificar JSON de especificacoes_tecnicas. Tratando como texto. Slug: '{slug}'.")
                    specs_dict = {"Descrição": specs_json_string} # Fallback
            
            produto_formatado['specs'] = specs_dict
            
            return render_template('oceano-produto-detalhe.html', produto=produto_formatado)
        else:
            print(f"ERRO FINAL: Produto não encontrado para '{url_busca_com_prefixo}' ou '{slug}'.")
            return "Produto não encontrado", 404
            
    except Exception as e:
        print(f"ERRO na rota /produtos/{slug}: {e}")
        traceback.print_exc()
        return "Erro ao carregar a página do produto", 500
    finally:
        if conn: conn.close()

@app.route('/')
def index_route():
    """Renderiza o 'index.html' dinamicamente usando Jinja2."""
    return render_template('index.html')


# =====================================================================
# --- PARTE 2: ROTAS DO PAINEL ADMIN B2B ('/admin' e '/api/oceano/admin') ---
# (Novas funcionalidades baseadas no 'suagrafica' e no seu 'schema_oceano')
# =====================================================================

# Rota para servir o HTML do painel de login/admin
@app.route('/admin')
def admin_panel_route():
    """Serve a página HTML do painel de administração."""
    # O 'admin.html' DEVE estar na pasta 'templates/'
    return render_template('admin.html')

# --- API: Login do Admin ---
@app.route('/api/oceano/admin/login', methods=['POST'])
def admin_login():
    """
    Verifica o login do admin na tabela 'oceano_admin'.
    (Constraint: Puxa usuários reais, sem placebo)
    """
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'erro': 'Usuário e senha são obrigatórios'}), 400

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Busca o usuário na tabela correta
        cur.execute("SELECT * FROM oceano_admin WHERE username = %s", (username,))
        admin_user = cur.fetchone()
        cur.close()
        
        # Verifica se o usuário existe E se a senha bate
        if admin_user and admin_user['chave_admin'] == password:
            # Senha correta! Gera um token JWT
            token = jwt.encode({
                'admin_id': admin_user['id'],
                'username': admin_user['username'],
                'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24) # Token expira em 24h
            }, app.config['SECRET_KEY'], algorithm="HS256")
            
            return jsonify({'mensagem': 'Login bem-sucedido!', 'token': token})
        else:
            # Usuário não encontrado ou senha incorreta
            return jsonify({'erro': 'Credenciais inválidas. Verifique usuário e senha.'}), 401

    except Exception as e:
        print(f"ERRO no login admin: {e}")
        traceback.print_exc()
        return jsonify({'erro': 'Erro interno no servidor.'}), 500
    finally:
        if conn: conn.close()


# --- API: Dashboard Stats ---
@app.route('/api/oceano/admin/dashboard_stats', methods=['GET'])
@token_required
def get_dashboard_stats():
    """Coleta estatísticas para os cards do admin."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Stat 1: Pedidos pendentes (Orçamentos)
        cur.execute("SELECT COUNT(id) FROM oceano_orcamentos WHERE status = 'Aguardando Orçamento' OR status = 'Aguardando Pagamento'")
        stat_pedidos = cur.fetchone()[0]
        
        # Stat 2: Produtos (Total, não apenas ativos, pois 'esta_ativo' foi removido)
        cur.execute("SELECT COUNT(id) FROM oceano_produtos")
        stat_produtos = cur.fetchone()[0]
        
        # Stat 3: Clientes
        cur.execute("SELECT COUNT(id) FROM oceano_clientes")
        stat_clientes = cur.fetchone()[0]
        
        cur.close()
        return jsonify({
            'stat_pedidos': stat_pedidos,
            'stat_produtos': stat_produtos,
            'stat_clientes': stat_clientes
        })
    except Exception as e:
        print(f"ERRO ao buscar stats: {e}")
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

# --- API: CRUD DE PRODUTOS (Centralizado) ---
# (Substitui o Colab)

@app.route('/api/oceano/admin/produtos', methods=['GET', 'POST'])
@token_required
def handle_produtos():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # --- GET (Listar todos) ---
        if request.method == 'GET':
            cur.execute("SELECT id, nome_produto, codigo_produto, categoria, imagem_principal_url FROM oceano_produtos ORDER BY id DESC")
            produtos = [format_db_data(dict(p)) for p in cur.fetchall()]
            cur.close()
            return jsonify(produtos)

        # --- POST (Criar novo) ---
        if request.method == 'POST':
            data = request.get_json()
            
            # Converte lista de galeria de string (separada por vírgula) para Array
            galeria_list = None
            if data.get('galeria_imagens'):
                galeria_list = [url.strip() for url in data['galeria_imagens'].split(',')]

            sql = """
            INSERT INTO oceano_produtos (
                nome_produto, codigo_produto, whatsapp_link_texto, descricao_curta, 
                descricao_longa, especificacoes_tecnicas, imagem_principal_url, 
                imagem_principal_alt, galeria_imagens, categoria, subcategoria, 
                url_slug, meta_title, meta_description
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
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

    except psycopg2.Error as e:
        if conn: conn.rollback()
        print(f"Erro de DB em handle_produtos: {e}")
        return jsonify({'erro': f'Erro de banco de dados: {e.pgerror}'}), 500
    except Exception as e:
        if conn: conn.rollback()
        print(f"Erro em handle_produtos: {e}")
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/produtos/<int:id>', methods=['GET', 'PUT', 'DELETE'])
@token_required
def handle_produto_id(id):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # --- GET (Buscar um) ---
        if request.method == 'GET':
            cur.execute("SELECT * FROM oceano_produtos WHERE id = %s", (id,))
            produto = cur.fetchone()
            if not produto:
                return jsonify({'erro': 'Produto não encontrado'}), 404
            cur.close()
            return jsonify(format_db_data(dict(produto)))

        # --- PUT (Atualizar um) ---
        if request.method == 'PUT':
            data = request.get_json()
            
            galeria_list = None
            if data.get('galeria_imagens'):
                galeria_list = [url.strip() for url in data['galeria_imagens'].split(',')]
            
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
                data.get('meta_title'), data.get('meta_description'),
                id
            ))
            conn.commit()
            cur.close()
            return jsonify({'mensagem': f'Produto ID {id} atualizado com sucesso!'})

        # --- DELETE (Excluir um) ---
        if request.method == 'DELETE':
            cur.execute("DELETE FROM oceano_produtos WHERE id = %s", (id,))
            conn.commit()
            cur.close()
            return jsonify({'mensagem': f'Produto ID {id} excluído com sucesso!'})

    except psycopg2.Error as e:
        if conn: conn.rollback()
        print(f"Erro de DB em handle_produto_id: {e}")
        return jsonify({'erro': f'Erro de banco de dados: {e.pgerror}'}), 500
    except Exception as e:
        if conn: conn.rollback()
        print(f"Erro em handle_produto_id: {e}")
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()


# --- API: CRUD DE CLIENTES ---

@app.route('/api/oceano/admin/clientes', methods=['GET', 'POST'])
@token_required
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
            sql = """
            INSERT INTO oceano_clientes (nome_cliente, email, telefone, cnpj_cpf, codigo_acesso)
            VALUES (%s, %s, %s, %s, %s) RETURNING id;
            """
            cur.execute(sql, (
                data.get('nome_cliente'), data.get('email'), data.get('telefone'),
                data.get('cnpj_cpf'), data.get('codigo_acesso')
            ))
            novo_id = cur.fetchone()['id']
            conn.commit()
            cur.close()
            return jsonify({'mensagem': 'Cliente criado com sucesso!', 'id': novo_id}), 201

    except psycopg2.IntegrityError as e:
        if conn: conn.rollback()
        if 'email' in str(e):
            return jsonify({'erro': 'Este Email já está cadastrado.'}), 409
        if 'cnpj_cpf' in str(e):
            return jsonify({'erro': 'Este CNPJ/CPF já está cadastrado.'}), 409
        if 'codigo_acesso' in str(e):
            return jsonify({'erro': 'Este Código de Acesso já está em uso.'}), 409
        return jsonify({'erro': f'Erro de integridade: {e.pgerror}'}), 409
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/clientes/<int:id>', methods=['DELETE'])
@token_required
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
        # Exceção de chave estrangeira (cliente tem orçamentos)
        if e.pgcode == '23503': 
            return jsonify({'erro': 'Não é possível excluir: este cliente já possui orçamentos ou pedidos registrados.'}), 409
        return jsonify({'erro': f'Erro de DB: {e.pgerror}'}), 500
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()


# --- API: CRUD DE ADMINS ---

@app.route('/api/oceano/admin/users', methods=['GET', 'POST'])
@token_required
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

    except psycopg2.IntegrityError as e:
        if conn: conn.rollback()
        return jsonify({'erro': 'Este nome de usuário já existe.'}), 409
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/users/<int:id>', methods=['DELETE'])
@token_required
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


# --- API: LÓGICA DE ORÇAMENTOS E PEDIDOS (V3 - Separados) ---

# --- API: ORÇAMENTOS ---

@app.route('/api/oceano/admin/orcamentos', methods=['GET'])
@token_required
def get_orcamentos():
    """Lista todos os orçamentos (Pedidos que NÃO SÃO 'Enviado' ou 'Concluído')."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        # Junta com clientes para pegar o nome
        sql = """
        SELECT o.*, c.nome_cliente 
        FROM oceano_orcamentos o
        LEFT JOIN oceano_clientes c ON o.cliente_id = c.id
        WHERE o.status NOT IN ('Enviado', 'Concluído', 'Cancelado')
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
@token_required
def handle_orcamento_id(id):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # --- GET (Buscar um Orçamento e seus Itens) ---
        if request.method == 'GET':
            orcamento = {}
            # 1. Pega os dados do Orçamento e Cliente
            sql_orc = """
            SELECT o.*, c.nome_cliente, c.email 
            FROM oceano_orcamentos o 
            LEFT JOIN oceano_clientes c ON o.cliente_id = c.id
            WHERE o.id = %s;
            """
            cur.execute(sql_orc, (id,))
            orcamento_data = cur.fetchone()
            if not orcamento_data:
                return jsonify({'erro': 'Orçamento não encontrado'}), 404
            
            orcamento = format_db_data(dict(orcamento_data))
            
            # 2. Pega os Itens do Orçamento
            sql_itens = """
            SELECT oi.*, p.nome_produto, p.codigo_produto 
            FROM oceano_orcamento_ilens oi
            LEFT JOIN oceano_produtos p ON oi.produto_id = p.id
            WHERE oi.orcamento_id = %s
            ORDER BY oi.id;
            """
            cur.execute(sql_itens, (id,))
            itens_data = cur.fetchall()
            orcamento['itens'] = [format_db_data(dict(i)) for i in itens_data]
            
            cur.close()
            return jsonify(orcamento)

        # --- PUT (Atualizar um Orçamento) ---
        if request.method == 'PUT':
            data = request.get_json()
            itens_atualizados = data.get('itens', [])

            # Inicia uma transação
            cur.execute("BEGIN;")

            # 1. Atualiza os dados principais do orçamento
            sql_update_orc = """
            UPDATE oceano_orcamentos SET
                status = %s,
                valor_frete = %s,
                valor_final_total = %s,
                chave_pix = %s,
                observacoes_admin = %s,
                data_atualizacao = CURRENT_TIMESTAMP
            WHERE id = %s;
            """
            cur.execute(sql_update_orc, (
                data.get('status'), data.get('valor_frete'), data.get('valor_final_total'),
                data.get('chave_pix'), data.get('observacoes_admin'), id
            ))
            
            # 2. Atualiza o preço unitário de cada item
            sql_update_item = "UPDATE oceano_orcamento_ilens SET preco_unitario_definido = %s WHERE id = %s AND orcamento_id = %s"
            for item in itens_atualizados:
                cur.execute(sql_update_item, (item.get('preco_unitario_definido'), item.get('id'), id))

            conn.commit() # Finaliza a transação
            cur.close()
            return jsonify({'mensagem': 'Orçamento atualizado com sucesso!'})

    except Exception as e:
        if conn: conn.rollback()
        print(f"Erro em handle_orcamento_id: {e}")
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()


# --- API: APROVAR ORÇAMENTO (Converter em Pedido) ---
@app.route('/api/oceano/admin/orcamentos/<int:id>/aprovar', methods=['POST'])
@token_required
def aprovar_orcamento(id):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Inicia transação
        cur.execute("BEGIN;")

        # 1. Pega os dados do Orçamento e seus Itens
        cur.execute("SELECT * FROM oceano_orcamentos WHERE id = %s", (id,))
        orcamento = cur.fetchone()
        if not orcamento:
            return jsonify({'erro': 'Orçamento não encontrado'}), 404
            
        cur.execute("SELECT * FROM oceano_orcamento_ilens WHERE orcamento_id = %s", (id,))
        itens_orcamento = cur.fetchall()

        # 2. Cria o novo PEDIDO (oceano_pedidos)
        sql_insert_pedido = """
        INSERT INTO oceano_pedidos (
            cliente_id, status, valor_frete, valor_final_total, 
            chave_pix, observacoes_admin, data_criacao, data_atualizacao
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        RETURNING id;
        """
        # O pedido já nasce "Em Produção"
        cur.execute(sql_insert_pedido, (
            orcamento['cliente_id'], 'Em Produção', orcamento['valor_frete'], 
            orcamento['valor_final_total'], orcamento['chave_pix'], 
            orcamento['observacoes_admin'], orcamento['data_criacao']
        ))
        novo_pedido_id = cur.fetchone()['id']
        
        # 3. Copia os Itens do Orçamento para os Itens de Pedido (oceano_pedido_ilens)
        sql_insert_item_pedido = """
        INSERT INTO oceano_pedido_ilens (
            pedido_id, produto_id, quantidade_solicitada, 
            observacoes_cliente, preco_unitario_definido
        ) VALUES (%s, %s, %s, %s, %s);
        """
        for item in itens_orcamento:
            cur.execute(sql_insert_item_pedido, (
                novo_pedido_id, item['produto_id'], item['quantidade_solicitada'],
                item['observacoes_cliente'], item['preco_unitario_definido']
            ))

        # 4. (OPCIONAL, mas recomendado) Deleta o Orçamento antigo
        # cur.execute("DELETE FROM oceano_orcamento_ilens WHERE orcamento_id = %s", (id,))
        # cur.execute("DELETE FROM oceano_orcamentos WHERE id = %s", (id,))
        # OU, melhor:
        # 4. Apenas muda o status do orçamento para "Convertido"
        cur.execute("UPDATE oceano_orcamentos SET status = 'Convertido em Pedido' WHERE id = %s", (id,))
        
        conn.commit() # Finaliza a transação
        cur.close()
        return jsonify({'mensagem': f'Orçamento {id} aprovado e convertido no Pedido #{novo_pedido_id}!'})

    except Exception as e:
        if conn: conn.rollback()
        print(f"Erro ao aprovar orçamento: {e}")
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()


# --- API: PEDIDOS (Aprovados) ---

@app.route('/api/oceano/admin/pedidos', methods=['GET'])
@token_required
def get_pedidos():
    """Lista todos os Pedidos APROVADOS (os que estão em oceano_pedidos)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
        SELECT p.*, c.nome_cliente 
        FROM oceano_pedidos p
        LEFT JOIN oceano_clientes c ON p.cliente_id = c.id
        ORDER BY p.data_atualizacao DESC;
        """
        cur.execute(sql)
        pedidos = [format_db_data(dict(p)) for p in cur.fetchall()]
        cur.close()
        return jsonify(pedidos)
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/oceano/admin/pedidos/<int:id>', methods=['GET', 'PUT'])
@token_required
def handle_pedido_id(id):
    """Gerencia um Pedido APROVADO (status, rastreio)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # --- GET (Buscar um Pedido e seus Itens) ---
        if request.method == 'GET':
            pedido = {}
            # 1. Pega os dados do Pedido e Cliente
            sql_ped = """
            SELECT p.*, c.nome_cliente, c.email 
            FROM oceano_pedidos p 
            LEFT JOIN oceano_clientes c ON p.cliente_id = c.id
            WHERE p.id = %s;
            """
            cur.execute(sql_ped, (id,))
            pedido_data = cur.fetchone()
            if not pedido_data:
                return jsonify({'erro': 'Pedido não encontrado'}), 404
            
            pedido = format_db_data(dict(pedido_data))
            
            # 2. Pega os Itens do Pedido
            sql_itens = """
            SELECT pi.*, p.nome_produto, p.codigo_produto 
            FROM oceano_pedido_ilens pi
            LEFT JOIN oceano_produtos p ON pi.produto_id = p.id
            WHERE pi.pedido_id = %s
            ORDER BY pi.id;
            """
            cur.execute(sql_itens, (id,))
            itens_data = cur.fetchall()
            pedido['itens'] = [format_db_data(dict(i)) for i in itens_data]
            
            cur.close()
            return jsonify(pedido)

        # --- PUT (Atualizar um Pedido - Status e Rastreio) ---
        if request.method == 'PUT':
            data = request.get_json()
            sql_update_ped = """
            UPDATE oceano_pedidos SET
                status = %s,
                codigo_rastreio = %s,
                observacoes_admin = %s,
                data_atualizacao = CURRENT_TIMESTAMP
            WHERE id = %s;
            """
            cur.execute(sql_update_ped, (
                data.get('status'), data.get('codigo_rastreio'), 
                data.get('observacoes_admin'), id
            ))
            
            conn.commit()
            cur.close()
            return jsonify({'mensagem': 'Pedido atualizado com sucesso!'})

    except Exception as e:
        if conn: conn.rollback()
        print(f"Erro em handle_pedido_id: {e}")
        return jsonify({'erro': str(e)}), 500
    finally:
        if conn: conn.close()


# =====================================================================
# --- PARTE 3: ROTAS DO PORTAL DO CLIENTE ---
# (Ainda não implementadas, mas aqui ficariam)
# =====================================================================
# @app.route('/portal-cliente/login', methods=['POST'])
# def cliente_login(): ...
#
# @app.route('/api/cliente/meus-pedidos', methods=['GET'])
# @cliente_token_required
# def cliente_get_pedidos(): ...
#
# @app.route('/api/cliente/novo-orcamento', methods=['POST'])
# def cliente_post_orcamento(): ...
#
# @app.route('/api/chat', methods=['POST'])
# def handle_chat(): ...


# =====================================================================
# --- PARTE 4: ROTAS PÚBLICAS (Fallback) ---
# (Deve vir por último)
# =====================================================================

@app.route('/<path:path>')
def serve_static_or_404(path):
    """
    Serve arquivos da pasta 'static/' (que agora é a pasta estática definida no Flask)
    ou retorna 404 se não for uma rota de API conhecida.
    """
    # Esta função só será chamada se a rota não for
    # '/', '/admin', '/produtos/<slug>', ou '/api/...'
    
    # O Flask já tenta servir da 'static_folder' automaticamente.
    # Se ele falhar e chegar aqui, é um 404.
    print(f"AVISO: Rota não encontrada (404) para: {path}")
    return "Página não encontrada", 404

# --- Execução do App ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    # Mude debug=True para desenvolvimento local
    app.run(host="0.0.0.0", port=port, debug=False)