package com.demo.repo;

import org.springframework.stereotype.Repository;

@Repository
public interface OrderRepository {
    Order findById(String id);
    Order save(Order order);
}
