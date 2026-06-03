
import asyncio

from config import get_logger
from config import KNOWLEDGEBASE_DATABASE_NAME, KNOWLEDGEBASE_SCHEMA_NAME

from database import connect_to_db
from models import TitanEmbeddingModel


logging = get_logger(__name__)

if __name__ == "__main__":

    async def main():
        pool = None

        try:
            pool = await connect_to_db(database_name=KNOWLEDGEBASE_DATABASE_NAME)

            async with pool.acquire() as conn:
                await conn.execute(f"""SET search_path TO {KNOWLEDGEBASE_SCHEMA_NAME};""")
                rows = await conn.fetch("""SELECT kbe_id, kbe_user_input
                                    FROM ai.knowledge_base_examples kbe
                                    WHERE kbe_user_input_embedding IS NULL;
                                    """)
                print("Rows fetched for embedding generation:", len(rows))

                if len(rows) > 0:
                    # Initialize the model
                    embedding_model = TitanEmbeddingModel()

                    print("\nEmbedding Generation Started.....")
                    # For each row generate embedding and update the user_input_embeddings column
                    for row in rows:
                        
                        id, user_input = row
                        
                        logging.info("Embedded Text: %s", user_input)

                        # Generate embedding in 768 dim np.array for the given user_input
                        embedding = embedding_model.generate_embedding(user_input)
                        # print(embedding)

                        # Update the kbe_user_input_embedding column with the generated embedding
                        await conn.execute("""UPDATE knowledge_base_examples
                                        SET kbe_user_input_embedding = $1
                                        WHERE kbe_id = $2;
                                        """, str(embedding), id)
                        
                    logging.info("Embeddings updated successfully!")

                else:
                    logging.info("All rows have embeddings already!")

        except Exception as e:
            logging.error(f"An error occurred:: {e}")
            
        finally:
            # Close the database connection
            print("Embedding Generation Ended.....")
            if pool:
                await pool.close()

    asyncio.run(main())